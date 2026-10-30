import sys

prev = 0


def step(i, v):
    global prev
    v = ((v << 6) | (v >> 2)) & 0xFF
    v = ((v << 3) | (v >> 5)) & 0xFF
    v ^= (i * 171 + 200) & 0xFF
    v ^= (i * 91 + 174) & 0xFF
    prev = v
    return v


data = sys.stdin.buffer.read()[:4096]
print(bytes(step(i, v) for i, v in enumerate(data)).hex())
