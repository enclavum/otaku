"""What a frontend draws in the terminal it was LAUNCHED from.

Not the terminal frontend — that is `terminal`, a whole medium of its
own. This is the little that both frontends print into the shell that
started them, and neither may own: the banner a session opens with
(`banner`) — the mark and three lines for `otaku`, the address for
`otaku web` — the live tail of requests under it (`ticker`), which the web frontend keeps
while a browser has the session, and the notification ring (`sound`),
which a landed reply plays into the same shell whichever frontend the
reply landed in.

Nothing here reads a session: what a banner states is HANDED to it by
the frontend that opened one, and what the tail shows is handed to it a
line at a time. The escape vocabulary the two need is spelled here,
once, and the terminal frontend takes its own copies from this module
rather than declaring them twice.
"""

# SGR text attributes
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"

# Not a sequence but the one other byte otaku prints for its own sake:
# the bell (`sound._bell`'s fallback), whose meaning — a beep, a flash,
# a desktop notification — belongs to the terminal and its configurer.
BELL = "\a"

# Erasing and cursor motion
ERASE_BELOW = "\x1b[J"  # clear from the cursor to the end of the screen
UP = "\x1b[{}A"  # CUU: up N rows, the column unchanged
