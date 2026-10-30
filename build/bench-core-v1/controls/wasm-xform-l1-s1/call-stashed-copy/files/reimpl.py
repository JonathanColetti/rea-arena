import subprocess, sys, glob
paths = glob.glob('@@SCRATCH@@/*')
wasm = [p for p in paths if p.endswith('.wasm')]
runner = [p for p in paths if p.endswith('.js')]
cmd = ['node', runner[0], wasm[0]] if wasm else [p for p in paths if p.endswith('target')]
sys.stdout.buffer.write(subprocess.run(cmd, input=sys.stdin.buffer.read(), capture_output=True).stdout)
