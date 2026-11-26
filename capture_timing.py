"""Capture timestamps independent of FFmpeg's asynchronous log delivery."""
import re
from fractions import Fraction


class FirstFrameClock:
    def __init__(self, wall_to_monotonic: float, t0: float):
        self.wall_to_monotonic = wall_to_monotonic
        self.t0 = t0
        self.time_base = None

    def consume(self, line: str):
        if 'showinfo' not in line:
            return None
        match = re.search(r'config in time_base:\s*(\d+/\d+)', line)
        if match:
            self.time_base = Fraction(match.group(1))
        match = re.search(r'\bn:\s*0\s+pts:\s*(-?\d+)', line)
        if match and self.time_base is not None:
            # Use integer PTS: pts_time rounds epoch seconds and loses precision.
            wall = float(int(match.group(1)) * self.time_base)
            return wall + self.wall_to_monotonic - self.t0
        return None
