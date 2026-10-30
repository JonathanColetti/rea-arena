#!/usr/bin/env node
// Runs an rea-arena WASM target: copies stdin into the module's input buffer (at most
// 4096 bytes), calls the exported run(length), and writes the output buffer to stdout.
// Usage: node run_wasm.js target.wasm < input
"use strict";
const fs = require("fs");

const wasmModule = new WebAssembly.Module(fs.readFileSync(process.argv[2]));
const wasm = new WebAssembly.Instance(wasmModule, {}).exports;
const input = fs.readFileSync(0).subarray(0, 4096);

new Uint8Array(wasm.memory.buffer).set(input, wasm.get_in());
const length = wasm.run(input.length);
const start = wasm.get_out();
const output = new Uint8Array(wasm.memory.buffer).slice(start, start + length);
process.stdout.write(Buffer.from(output));
