"""Isolated Windows notice player. No Discord, schedule or persistent files.

MediaEnded is the only success signal. Position/duration and process exit are
never used as evidence of successful playback. Loading does not start audio.
"""
import base64
import os
import subprocess
import threading
import time


# All WPF access stays on the child STA Dispatcher. The reader thread only
# posts commands. Paths travel as environment data, never executable text.
PLAYER_SOURCE = r'''
using System;
using System.Threading;
using System.Windows.Media;
using System.Windows.Threading;
public static class BossNoticePlayer {
    public static void Run(string path) {
        var dispatcher = Dispatcher.CurrentDispatcher;
        var player = new MediaPlayer();
        string state = "preparing";
        Action<string, string> report = (id, value) => {
            Console.WriteLine(id + "\t" + value); Console.Out.Flush();
        };
        Action<string> change = value => { state = value; report("0", value); };
        Action fail = () => {
            player.IsMuted = true; player.Stop(); player.Close(); change("failed");
        };
        Action close = () => {
            player.IsMuted = true; player.Stop(); player.Close();
            change("stopped"); dispatcher.BeginInvokeShutdown(DispatcherPriority.Normal);
        };
        var started = DateTime.UtcNow;
        var watchdog = new DispatcherTimer();
        watchdog.Interval = TimeSpan.FromMilliseconds(250);
        watchdog.Tick += (s, e) => {
            // A stuck decoder never earns a success receipt.
            if ((state == "preparing" && (DateTime.UtcNow - started).TotalSeconds > 15)
                || (DateTime.UtcNow - started).TotalMinutes > 30) { fail(); close(); }
        };
        player.MediaOpened += (s, e) => {
            if (state != "preparing") return;
            if (!player.HasAudio) { fail(); return; }
            change("ready");
        };
        player.MediaFailed += (s, e) => { fail(); };
        player.MediaEnded += (s, e) => {
            if (state != "playing" && state != "paused") return;
            player.IsMuted = true; player.Stop(); change("completed");
        };
        var reader = new Thread(() => {
            try {
                string line;
                while ((line = Console.ReadLine()) != null) {
                    var parts = line.Split('\t');
                    int serial;
                    if (parts.Length != 2 || !Int32.TryParse(parts[0], out serial) || serial <= 0) continue;
                    string id = parts[0], command = parts[1];
                    dispatcher.BeginInvoke(new Action(() => {
                        try {
                            if (command == "play" && (state == "ready" || state == "paused")) {
                                player.Play(); player.IsMuted = false; change("playing");
                            } else if (command == "pause" && state == "playing") {
                                player.IsMuted = true;
                                if (!player.CanPause) { fail(); } else { player.Pause(); change("paused"); }
                            } else if (command == "stop") { close(); }
                            report(id, state);
                        } catch { fail(); report(id, "failed"); }
                    }));
                }
            } finally { dispatcher.BeginInvoke(new Action(close)); }
        });
        reader.IsBackground = true;
        try {
            player.IsMuted = true;
            player.Open(new Uri(path, UriKind.Absolute));
            reader.Start(); watchdog.Start(); Dispatcher.Run();
        } finally { watchdog.Stop(); player.IsMuted = true; player.Close(); }
    }
}
'''


def spawn_player(path):
    if os.name != "nt":
        raise RuntimeError("알리미 로컬 재생은 Windows에서 지원합니다.")
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise ValueError("알리미 음성 파일이 없습니다.")
    script = ("$ErrorActionPreference = 'Stop'\n"
              "Add-Type -AssemblyName PresentationCore,WindowsBase\n"
              "$source = @'\n" + PLAYER_SOURCE + "\n'@\n"
              "Add-Type -TypeDefinition $source -ReferencedAssemblies PresentationCore,WindowsBase\n"
              "[BossNoticePlayer]::Run($env:BOSS_NOTICE_AUDIO_PATH)\n")
    command = ["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Sta", "-EncodedCommand",
               base64.b64encode(script.encode("utf-16le")).decode("ascii")]
    return subprocess.Popen(command, env=dict(os.environ, BOSS_NOTICE_AUDIO_PATH=path),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, encoding="ascii", bufsize=1,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


class LocalPlayer:
    """Bounded command acknowledgments; a wedged child must be killed, not trusted."""
    def __init__(self, path, *, spawn=spawn_player, clock=time.monotonic):
        self.clock = clock
        self.process = spawn(path)
        self.condition = threading.Condition()
        self.command_lock = threading.Lock()
        self.state = "preparing"
        self.created = clock()
        self.serial = 0
        self.acks = {}
        self.reader = threading.Thread(target=self._read, name="notice-player-receipts", daemon=True)
        self.reader.start()

    def _read(self):
        try:
            for line in self.process.stdout:
                parts = line.strip().split("\t")
                if len(parts) != 2 or not parts[0].isdigit():
                    continue
                number, state = int(parts[0]), parts[1]
                if state not in {"ready", "playing", "paused", "completed", "failed", "stopped"}:
                    continue
                with self.condition:
                    if number == 0:
                        if self.state not in {"completed", "failed", "stopped"}:
                            self.state = state
                    elif number == self.serial:
                        self.acks[number] = state
                    self.condition.notify_all()
        finally:
            with self.condition:
                if self.state not in {"completed", "stopped"}:
                    self.state = "failed"
                self.condition.notify_all()
            self.process.stdout.close()

    def status(self):
        with self.condition:
            if self.process.poll() is not None and self.state not in {"completed", "stopped"}:
                self.state = "failed"
            # Covers a child stuck before its own watchdog was created (Add-Type).
            if self.state == "preparing" and self.clock() - self.created > 30:
                self.state = "failed"
            return self.state

    def command(self, command, expected, timeout=0.3):
        if command not in {"play", "pause", "stop"}:
            raise ValueError("지원하지 않는 재생 명령입니다.")
        with self.command_lock:
            with self.condition:
                if self.process.poll() is not None:
                    return False
                self.serial += 1
                number = self.serial
                self.acks.clear()
            try:
                self.process.stdin.write(f"{number}\t{command}\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                return False
            with self.condition:
                self.condition.wait_for(lambda: number in self.acks or self.process.poll() is not None,
                                        timeout=timeout)
                return self.acks.pop(number, None) in expected

    def play(self):
        return self.command("play", {"playing"})

    def pause(self):
        return self.command("pause", {"paused", "completed", "stopped", "failed"})

    def stop(self):
        # Killing this owned process cannot stop a boss player or a preview.
        try:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=0.3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=0.3)
            silent = self.process.poll() is not None
        except (OSError, subprocess.TimeoutExpired):
            silent = self.process.poll() is not None
        if silent:
            with self.condition:
                self.state = "stopped"
                self.condition.notify_all()
            # Reader closes stdout after EOF; don't contend on a blocked readline.
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass
        return silent
