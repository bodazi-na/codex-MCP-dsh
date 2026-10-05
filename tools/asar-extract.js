#!/usr/bin/env node
/**
 * Minimal asar reader/extractor.
 *
 *   node asar-extract.js --list <prefix>            # list entries under a prefix
 *   node asar-extract.js <innerPath> <outFile>      # extract one file
 *
 * The archive layout used by Electron: [4B pickle size=4][4B header pickle
 * size][header JSON (headerSize bytes)] then the data section. File entries
 * carry {size, offset} where offset is relative to the data section start.
 */
const fs = require('fs');
const path = require('path');

const ARCHIVE = process.env.DSH_ASAR || 'D:\\DSH\\resources\\app.asar';

function readHeader(archive) {
  const fd = fs.openSync(archive, 'r');
  const head = Buffer.alloc(16);
  fs.readSync(fd, head, 0, 16, 0);
  const jsonSize = head.readUInt32LE(12);
  const jsonBuf = Buffer.alloc(jsonSize);
  fs.readSync(fd, jsonBuf, 0, jsonSize, 16);
  const header = JSON.parse(jsonBuf.toString('utf8'));
  const dataStart = 16 + jsonSize;
  return { fd, header, dataStart };
}

/** Walk the tree, calling fn(fullPath, entry) for every file. */
function walk(header, fn) {
  const stack = [['', header]];
  while (stack.length) {
    const [prefix, node] = stack.pop();
    for (const name of Object.keys(node.files || {})) {
      const child = node.files[name];
      const full = prefix ? `${prefix}/${name}` : name;
      if (child.files) stack.push([full, child]);
      else fn(full, child);
    }
  }
}

function main() {
  const [mode, a, b] = process.argv.slice(2);
  const { fd, header, dataStart } = readHeader(ARCHIVE);
  try {
    if (mode === '--list') {
      const prefix = (a || '').replace(/^\/+/, '');
      const hits = [];
      walk(header, (full, entry) => {
        if (full.startsWith(prefix)) hits.push([full, entry.size]);
      });
      hits.sort((x, y) => y[1] - x[1]);
      let total = 0;
      for (const [full, size] of hits) {
        total += size;
        console.log(`${String(size).padStart(12)}  ${full}`);
      }
      console.log(`--- ${hits.length} files, ${(total / 1048576).toFixed(1)} MB total ---`);
      return;
    }
    if (!mode || !a) {
      console.error('usage: node asar-extract.js --list <prefix> | <innerPath> <outFile>');
      process.exit(2);
    }
    const inner = mode.replace(/^\/+/, '');
    let found = null;
    walk(header, (full, entry) => {
      if (full === inner) found = entry;
    });
    if (!found) {
      console.error(`not found in archive: ${inner}`);
      process.exit(3);
    }
    const out = path.resolve(a);
    fs.mkdirSync(path.dirname(out), { recursive: true });
    const buf = Buffer.alloc(found.size);
    fs.readSync(fd, buf, 0, found.size, dataStart + Number(found.offset));
    fs.writeFileSync(out, buf);
    console.log(`extracted ${found.size} bytes -> ${out}`);
  } finally {
    fs.closeSync(fd);
  }
}

main();
