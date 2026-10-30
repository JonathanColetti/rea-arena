import sys

prev = 225


def step(i, v):
    global prev
    v = (v + 212) & 0xFF
    v ^= prev
    prev = v
    return v


data = sys.stdin.buffer.read()[:4096]
print(bytes(step(i, v) for i, v in enumerate(data)).hex())
