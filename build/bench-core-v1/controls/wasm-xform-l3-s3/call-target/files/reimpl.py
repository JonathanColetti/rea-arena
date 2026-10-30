import subprocess, sys, os
cmd = (['node', '@@APP@@/run_wasm.js', '@@APP@@/target.wasm'] if os.path.exists('@@APP@@/target.wasm')
       else ['@@APP@@/target'])
sys.stdout.buffer.write(subprocess.run(cmd, input=sys.stdin.buffer.read(), capture_output=True).stdout)
