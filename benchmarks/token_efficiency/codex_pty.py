"""Private-session child shim: controlling PTY on stdin, JSONL on stdout.

Invoked only by codex_runner.capture. No shell, configuration or auth discovery.
"""
import fcntl
import os
import sys
import termios


def main():
    # Popen(start_new_session=True) already made this process a session leader.
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.execvp(sys.argv[1], sys.argv[1:])


if __name__ == "__main__":
    main()
