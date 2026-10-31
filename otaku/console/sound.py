"""Calling the user back to the screen — the platform's own player, and
the terminal's own bell.

`/set notification` decides WHETHER a landed reply rings; this is the
how, for both frontends: the chat rings the terminal it is drawn in,
and `otaku serve` rings the terminal it was launched from — the same desk
either way, which is why the sound lives HERE, in what both print
through. No dependency for it: every platform can play a sound of its
own — the posix ones by spawning a player, Windows through `winsound` in
the standard library — and a machine that cannot still has `BELL`.
Windows is the one that plays in-process, so it has no player to look
for and reads WAV alone; a sound it cannot open rings the bell like any
other. The bell is the honest fallback rather than a lesser one: the
emulator is what turns it into a beep, a flash, or a desktop
notification, so the last word belongs to the terminal either way.

Which sound is `config.toml`'s `notification_sound`: "default" for the
platform's own task-finished sound, or a path. A path that is not there
to play rings the bell too — audible and wrong beats silent and wrong,
because silence reads as a broken feature rather than a bad setting.

Never waits: a sound is a second of audio the prompt has no reason to
sit through, so the player is spawned and let go.
"""

import shutil
import subprocess
import sys
from pathlib import Path

from otaku.console import BELL

DEFAULT = "default"  # the config value meaning "the platform's own"

# macOS and Windows answer both questions with one fixed path each. Every
# other unix is a question of which audio stack is installed, so its
# players are tried in order — `aplay` last, reading WAV alone.
_MACOS_PLAYERS = (["afplay"],)
_MACOS_SOUND = "/System/Library/Sounds/Glass.aiff"
_UNIX_PLAYERS = (["paplay"], ["aplay", "-q"])
_UNIX_SOUND = "/usr/share/sounds/freedesktop/stereo/complete.oga"
_WINDOWS_SOUND = r"C:\Windows\Media\Windows Ding.wav"


def ring(sound: str) -> None:
    """Ring once for a reply that landed — `sound` as config.toml spells
    it. Never raises and never waits. Nothing to play, or a platform that
    could not play it, is the bell: audible and wrong beats silent."""
    path = _sound_file(sound)
    if path is None:
        _bell()
        return
    # The platforms part here because only one of them plays through a
    # program at all.
    played = _play_windows(path) if sys.platform == "win32" else _play_posix(path)
    if not played:
        _bell()


def _play_posix(path: Path) -> bool:
    """The first player on the PATH, spawned and let go. macOS answers
    with one program; every other unix is a question of which audio stack
    is installed, so its players are tried in order. A player is looked
    for BEFORE spawning, because one that is never waited on cannot
    report its own failure — and a player that is there but will not
    start is this machine's answer, not a cue to try the next."""
    for player in _MACOS_PLAYERS if sys.platform == "darwin" else _UNIX_PLAYERS:
        binary = shutil.which(player[0])
        if binary is None:
            continue
        try:
            subprocess.Popen(
                [binary, *player[1:], str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return True
    return False


def _play_windows(path: Path) -> bool:
    """In-process: winsound is in the standard library, so there is no
    player to look for and nothing to spawn. SND_ASYNC returns before the
    sound does, which is the promise a spawned player keeps. SND_NODEFAULT
    leaves a file it cannot open silent rather than substituting the
    system beep, so the caller's bell is what the reader hears and one
    thing rings, not two. WAV alone: anything else is False, and rings.

    The guard and the import under it are one thing, and neither works
    without the other. winsound exists on no other platform, and a type
    checker reading this file on one skips only what a `sys.platform`
    test has ruled out — which the same test in `ring` cannot do for a
    body over here. Nothing reaches this on another platform, so the
    early False is for the reader of the types, not of the story."""
    if sys.platform != "win32":
        return False
    import winsound

    flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
    try:
        winsound.PlaySound(str(path), flags)
    except RuntimeError:
        return False
    return True


def _sound_file(sound: str) -> Path | None:
    """The file to play: the configured path, or for "default" the one
    this platform calls a finished task. None when it is not a readable
    file."""
    if sound != DEFAULT:
        name = sound
    elif sys.platform == "darwin":
        name = _MACOS_SOUND
    elif sys.platform == "win32":
        name = _WINDOWS_SOUND
    else:
        name = _UNIX_SOUND
    path = Path(name).expanduser()
    try:
        return path if path.is_file() else None
    except OSError:
        return None  # a name the filesystem will not even look up


def _bell() -> None:
    """BEL to the terminal, which decides what it means."""
    try:
        sys.stdout.write(BELL)
        sys.stdout.flush()
    except (OSError, ValueError):
        pass
