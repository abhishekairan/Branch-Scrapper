//! Supervision of the Python sidecar (`python -m ghscraper.sidecar`).
//!
//! Contract (docs/sidecar-protocol.md section 1):
//! * the token travels in the `GHOSTSCRAPER_TOKEN` environment variable (never on the command line),
//! * the sidecar prints one `READY {...}` line on stdout when it listens,
//! * **closing its stdin** is the parent-death signal: it shuts down cleanly. So the stdin pipe is kept
//!   open for as long as the sidecar should live, and closing it is also how we stop it.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc;
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter, Manager};

const READY_TIMEOUT: Duration = Duration::from_secs(20);
const STOP_GRACE: Duration = Duration::from_secs(5);
const TOKEN_ENV: &str = "GHOSTSCRAPER_TOKEN";

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// What the webview needs to connect: `ws://127.0.0.1:<port>` + the auth token.
#[derive(Clone, Serialize)]
pub struct SidecarInfo {
    pub port: u16,
    pub token: String,
    pub version: String,
}

#[derive(Deserialize)]
struct Ready {
    port: u16,
    pid: u32,
    version: String,
}

#[derive(Clone, Serialize)]
struct ExitedPayload {
    code: Option<i32>,
    message: String,
}

enum Phase {
    Starting,
    Ready(SidecarInfo),
    /// Spawn failed, READY never came, or the process exited. Holds a human-readable reason.
    Failed(String),
    /// Stopped on purpose (restart in progress or app exiting).
    Stopped,
}

struct Running {
    child: Child,
    stdin: Option<ChildStdin>,
    /// Generation counter value this process was started with (to ignore late events of old processes).
    generation: u64,
    /// pid of the real sidecar from the READY line (a venv `python.exe` is only a launcher).
    real_pid: u32,
}

struct Inner {
    phase: Phase,
    running: Option<Running>,
    generation: u64,
}

pub struct Sidecar {
    inner: Mutex<Inner>,
    changed: Condvar,
    /// Serialises start / stop / restart so they never interleave.
    lifecycle: Mutex<()>,
}

struct Spec {
    program: PathBuf,
    args: Vec<String>,
    cwd: Option<PathBuf>,
    env: Vec<(String, String)>,
}

impl Sidecar {
    pub fn new() -> Arc<Sidecar> {
        Arc::new(Sidecar {
            inner: Mutex::new(Inner { phase: Phase::Starting, running: None, generation: 0 }),
            changed: Condvar::new(),
            lifecycle: Mutex::new(()),
        })
    }

    /// Blocks until the sidecar is ready (or failed). Used by the `sidecar_info` command.
    pub fn wait_info(&self, timeout: Duration) -> Result<SidecarInfo, String> {
        let deadline = Instant::now() + timeout;
        let mut g = self.inner.lock().unwrap();
        loop {
            match &g.phase {
                Phase::Ready(info) => return Ok(info.clone()),
                Phase::Failed(msg) => return Err(msg.clone()),
                Phase::Stopped => return Err("the sidecar is stopped".into()),
                Phase::Starting => {}
            }
            let now = Instant::now();
            if now >= deadline {
                return Err("timed out waiting for the sidecar to start".into());
            }
            g = self.changed.wait_timeout(g, deadline - now).unwrap().0;
        }
    }

    fn set_phase(&self, phase: Phase) {
        self.inner.lock().unwrap().phase = phase;
        self.changed.notify_all();
    }

    /// Spawn the sidecar and wait for its READY line.
    pub fn start(self: &Arc<Self>, app: &AppHandle) -> Result<SidecarInfo, String> {
        let _life = self.lifecycle.lock().unwrap();
        self.start_locked(app)
    }

    /// Stop (stdin close, 5 s grace, then kill) and start again with a fresh token.
    pub fn restart(self: &Arc<Self>, app: &AppHandle) -> Result<SidecarInfo, String> {
        let _life = self.lifecycle.lock().unwrap();
        self.stop_locked();
        self.start_locked(app)
    }

    /// Stop on purpose: close stdin, wait up to 5 s, then kill the process tree.
    pub fn stop(&self) {
        let _life = self.lifecycle.lock().unwrap();
        self.stop_locked();
    }

    fn start_locked(self: &Arc<Self>, app: &AppHandle) -> Result<SidecarInfo, String> {
        let generation = {
            let mut g = self.inner.lock().unwrap();
            g.generation += 1;
            g.phase = Phase::Starting;
            g.generation
        };
        self.changed.notify_all();
        let _ = app.emit("sidecar-starting", ());
        match self.spawn(app, generation) {
            Ok(info) => Ok(info),
            Err(msg) => {
                let msg = format!("{msg}{}", log_hint(app));
                log::error!("sidecar start failed: {msg}");
                self.set_phase(Phase::Failed(msg.clone()));
                let _ = app.emit("sidecar-exited", ExitedPayload { code: None, message: msg.clone() });
                Err(msg)
            }
        }
    }

    fn spawn(self: &Arc<Self>, app: &AppHandle, generation: u64) -> Result<SidecarInfo, String> {
        let spec = resolve_command(app)?;
        let token = new_token()?;

        let mut cmd = Command::new(&spec.program);
        cmd.args(&spec.args)
            .env(TOKEN_ENV, &token)
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::piped()) // kept open: closing it tells the sidecar to exit
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        for (k, v) in &spec.env {
            cmd.env(k, v);
        }
        if let Some(cwd) = &spec.cwd {
            cmd.current_dir(cwd);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }

        log::info!("starting sidecar: {} {}", spec.program.display(), spec.args.join(" "));
        let mut child = cmd
            .spawn()
            .map_err(|e| format!("could not start the sidecar ({}): {e}", spec.program.display()))?;
        let stdin = child.stdin.take();
        let stdout = child.stdout.take().ok_or("sidecar stdout unavailable")?;
        let stderr = child.stderr.take().ok_or("sidecar stderr unavailable")?;

        // stderr -> log
        std::thread::Builder::new()
            .name("sidecar-stderr".into())
            .spawn(move || {
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    log::info!(target: "sidecar", "{line}");
                }
            })
            .map_err(|e| e.to_string())?;

        // stdout: first the READY line, then drain (and log) until EOF = the sidecar is gone.
        let (tx, rx) = mpsc::channel::<Ready>();
        let me = Arc::clone(self);
        let app2 = app.clone();
        std::thread::Builder::new()
            .name("sidecar-stdout".into())
            .spawn(move || {
                let mut tx = Some(tx);
                for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                    if tx.is_some() {
                        if let Some(json) = line.strip_prefix("READY ") {
                            match serde_json::from_str::<Ready>(json) {
                                Ok(r) => {
                                    let _ = tx.take().unwrap().send(r);
                                    continue;
                                }
                                Err(e) => log::warn!("unparseable READY line ({e}): {line}"),
                            }
                        }
                    }
                    log::info!(target: "sidecar", "{line}");
                }
                me.on_stdout_eof(&app2, generation);
            })
            .map_err(|e| e.to_string())?;

        let ready = match rx.recv_timeout(READY_TIMEOUT) {
            Ok(r) => r,
            Err(e) => {
                kill_tree(&mut child, None);
                let _ = child.wait();
                return Err(match e {
                    mpsc::RecvTimeoutError::Timeout => {
                        format!("the sidecar did not report READY within {} s", READY_TIMEOUT.as_secs())
                    }
                    mpsc::RecvTimeoutError::Disconnected => {
                        "the sidecar exited before it was ready (see the log for its stderr)".to_string()
                    }
                });
            }
        };
        log::info!("sidecar ready: port {} pid {} version {}", ready.port, ready.pid, ready.version);

        let info = SidecarInfo { port: ready.port, token, version: ready.version };
        {
            let mut g = self.inner.lock().unwrap();
            g.running = Some(Running { child, stdin, generation, real_pid: ready.pid });
            g.phase = Phase::Ready(info.clone());
        }
        self.changed.notify_all();
        let _ = app.emit("sidecar-ready", info.clone());
        Ok(info)
    }

    /// The sidecar's stdout closed. If we did not ask for it, report an unexpected exit.
    fn on_stdout_eof(&self, app: &AppHandle, generation: u64) {
        let taken = {
            let mut g = self.inner.lock().unwrap();
            match g.running.as_ref() {
                Some(r) if r.generation == generation => g.running.take(),
                _ => None, // stopped on purpose, or a newer process owns the slot
            }
        };
        let Some(mut running) = taken else { return };
        drop(running.stdin.take());
        let code = wait_timeout(&mut running.child, Duration::from_secs(2)).flatten();
        if code.is_none() {
            kill_tree(&mut running.child, Some(running.real_pid));
            let _ = running.child.wait();
        }
        let message = match code {
            Some(0) => "the sidecar exited".to_string(),
            Some(c) => format!("the sidecar exited unexpectedly (exit code {c})"),
            None => "the sidecar exited unexpectedly".to_string(),
        };
        let message = format!("{message}{}", log_hint(app));
        log::error!("{message}");
        self.set_phase(Phase::Failed(message.clone()));
        let _ = app.emit("sidecar-exited", ExitedPayload { code, message });
    }

    fn stop_locked(&self) {
        let taken = {
            let mut g = self.inner.lock().unwrap();
            g.phase = Phase::Stopped;
            g.running.take()
        };
        self.changed.notify_all();
        let Some(mut running) = taken else { return };
        log::info!("stopping sidecar (closing stdin)");
        drop(running.stdin.take()); // EOF on its stdin = clean shutdown
        match wait_timeout(&mut running.child, STOP_GRACE) {
            Some(_) => log::info!("sidecar exited cleanly"),
            None => {
                log::warn!("sidecar did not exit within {} s; killing it", STOP_GRACE.as_secs());
                kill_tree(&mut running.child, Some(running.real_pid));
                let _ = running.child.wait();
            }
        }
    }
}

/// " See the log: <path>" for error messages shown in the UI. The shell log also receives everything the
/// sidecar writes to stdout/stderr; the sidecar's own rotating log is `sidecar.log` in its data directory.
fn log_hint(app: &AppHandle) -> String {
    match app.path().app_log_dir() {
        Ok(dir) => format!(" See the log: {}", dir.join("shell.log").display()),
        Err(_) => String::new(),
    }
}

/// `Some(exit code)` if the child exited within `timeout` (`Some(None)` = ended by a signal), else `None`.
fn wait_timeout(child: &mut Child, timeout: Duration) -> Option<Option<i32>> {
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Some(status.code()),
            Ok(None) => {}
            Err(_) => return Some(None),
        }
        if Instant::now() >= deadline {
            return None;
        }
        std::thread::sleep(Duration::from_millis(40));
    }
}

/// Kill the launcher and everything below it (a venv `python.exe` spawns the real interpreter).
fn kill_tree(child: &mut Child, real_pid: Option<u32>) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let mut pids = vec![child.id()];
        pids.extend(real_pid);
        for pid in pids {
            let _ = Command::new("taskkill")
                .args(["/PID", &pid.to_string(), "/T", "/F"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .creation_flags(CREATE_NO_WINDOW)
                .status();
        }
    }
    #[cfg(not(windows))]
    {
        // TODO(phase 7): kill the process group on Linux/macOS.
        if let Some(pid) = real_pid {
            let _ = Command::new("kill").args(["-9", &pid.to_string()]).status();
        }
    }
    let _ = child.kill();
}

/// 32 random bytes from the OS CSPRNG, hex encoded (64 chars; the sidecar needs >= 32).
fn new_token() -> Result<String, String> {
    let mut buf = [0u8; 32];
    getrandom::fill(&mut buf).map_err(|e| format!("no secure random source: {e}"))?;
    Ok(buf.iter().map(|b| format!("{b:02x}")).collect())
}

// ------------------------------------------------------------------ how to launch it

#[cfg(debug_assertions)]
fn resolve_command(_app: &AppHandle) -> Result<Spec, String> {
    // Dev builds only. CARGO_MANIFEST_DIR = <repo>/app/src-tauri.
    let repo_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .and_then(|p| p.parent())
        .map(PathBuf::from)
        .ok_or("cannot resolve the repository root")?;

    let (program, mut args) = match std::env::var("GHOSTSCRAPER_SIDECAR_CMD") {
        Ok(cmd) if !cmd.trim().is_empty() => {
            let mut parts = split_args(&cmd);
            if parts.is_empty() {
                return Err("GHOSTSCRAPER_SIDECAR_CMD is empty".into());
            }
            let program = PathBuf::from(parts.remove(0));
            (program, parts)
        }
        _ => {
            let python = if cfg!(windows) {
                repo_root.join(".venv").join("Scripts").join("python.exe")
            } else {
                repo_root.join(".venv").join("bin").join("python")
            };
            if !python.exists() {
                return Err(format!(
                    "the repo virtualenv python was not found at {}; create the venv or set GHOSTSCRAPER_SIDECAR_CMD",
                    python.display()
                ));
            }
            (python, vec!["-m".to_string(), "ghscraper.sidecar".to_string()])
        }
    };

    let mut env = Vec::new();
    if std::env::var("GHOSTSCRAPER_DEV_TEST_MODE").as_deref() == Ok("1") {
        // Development only: the sidecar honours the fake pool only together with --test-mode.
        args.push("--test-mode".into());
        env.push(("GHOSTSCRAPER_FAKE_POOL".to_string(), "1".to_string()));
        log::warn!("GHOSTSCRAPER_DEV_TEST_MODE=1: starting the sidecar in test mode (fake browser pool)");
    }
    Ok(Spec { program, args, cwd: Some(repo_root), env })
}

#[cfg(not(debug_assertions))]
fn resolve_command(app: &AppHandle) -> Result<Spec, String> {
    // The PyInstaller onedir build is shipped as bundle resources (tauri.conf.json > bundle > resources):
    // <resource dir>/sidecar/branch-scrapper-sidecar.exe plus its _internal folder. No env override in release
    // builds: the program to run must not be steerable from the environment.
    let exe_name = if cfg!(windows) { "branch-scrapper-sidecar.exe" } else { "branch-scrapper-sidecar" };
    let dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("cannot resolve the app resource directory: {e}"))?
        .join("sidecar");
    let program = dir.join(exe_name);
    if !program.is_file() {
        return Err(format!(
            "the bundled backend is missing ({}); reinstall the app",
            program.display()
        ));
    }
    Ok(Spec { program, args: Vec::new(), cwd: None, env: Vec::new() })
}

/// Split a command line on whitespace; double quotes group words (no escapes; Windows paths are fine).
#[cfg(debug_assertions)]
fn split_args(s: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut cur = String::new();
    let mut in_quotes = false;
    let mut any = false;
    for ch in s.chars() {
        match ch {
            '"' => {
                in_quotes = !in_quotes;
                any = true;
            }
            c if c.is_whitespace() && !in_quotes => {
                if any || !cur.is_empty() {
                    out.push(std::mem::take(&mut cur));
                    any = false;
                }
            }
            c => cur.push(c),
        }
    }
    if any || !cur.is_empty() {
        out.push(cur);
    }
    out
}
