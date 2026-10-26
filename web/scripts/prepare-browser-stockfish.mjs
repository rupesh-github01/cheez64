// Copy only the pinned, unmodified single-thread lite engine. Never execute it here.
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const pkg=path.join(root,'node_modules/stockfish');
if(JSON.parse(fs.readFileSync(path.join(pkg,'package.json'),'utf8')).version!=='19.0.1')throw new Error('Stockfish package version mismatch');
const target=path.join(root,'public/engines/stockfish-19.0.1');fs.mkdirSync(target,{recursive:true});
const expected={
 'stockfish-19-lite-single.js':'ccb130d7ff29408531c47d859a49f91d59d4f482d05a53eb1a3231a4f624c91a',
 'stockfish-19-lite-single.wasm':'ee4f115bdb2d32cadf96cada104a05b5ec257d7ad6f57582190b49bc71f096de',
};
for(const [name,hash] of Object.entries(expected)){
 const bytes=fs.readFileSync(path.join(pkg,'bin',name));
 if(createHash('sha256').update(bytes).digest('hex')!==hash)throw new Error(`Stockfish artifact integrity mismatch: ${name}`);
 const destination=path.join(target,name);
 if(!fs.existsSync(destination)||!fs.readFileSync(destination).equals(bytes))fs.writeFileSync(destination,bytes,{mode:0o644});
}
for(const [from,to] of [['Copying.txt','COPYING.txt'],['README.md','UPSTREAM_README.md']])fs.copyFileSync(path.join(pkg,from),path.join(target,to));
console.log('Verified Stockfish 19.0.1 lite single-thread assets (SHA-256).');
for(const [name,hash] of Object.entries({
 'stockfish-js-v19.0.1-source.tar.gz':'a18aa8a871f5265014b110b3e07804ff3637899d2e189be6ded2b99e2c17a10e',
 'nn-61e7af4bb97d.nnue':'61e7af4bb97d51eeeb25d322916f86513b5cd3a827ce189c98c6e31946f99e5b'
})){
 const file=path.join(target,name);
 if(!fs.existsSync(file)||createHash('sha256').update(fs.readFileSync(file)).digest('hex')!==hash)throw new Error(`Required GPL source material missing or changed: ${name}`);
}
