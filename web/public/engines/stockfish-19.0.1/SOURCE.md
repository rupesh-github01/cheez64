# Stockfish.js 19.0.1 — browser engine distribution

This folder serves the unmodified `stockfish-19-lite-single.js` and matching WASM
from the npm package `stockfish@19.0.1`, by Nathan Rugg / Chess.com, LLC, based on
Stockfish by the Stockfish authors. Neural network by Chris Bao (sscg13).

The engine is free software under **GNU GPL version 3**. See [COPYING.txt](./COPYING.txt),
[AUTHORS](./AUTHORS), and [upstream README](./UPSTREAM_README.md).
It comes WITHOUT ANY WARRANTY. Engine output is an estimate, not proof of a best move.

## Corresponding source and build inputs (same server, no charge)

- [Exact v19.0.1 source archive](./stockfish-js-v19.0.1-source.tar.gz)
- [Lite NNUE build input nn-61e7af4bb97d.nnue](./nn-61e7af4bb97d.nnue)
- [Artifact/source SHA-256 manifest and npm SHA-512 integrity](./integrity.json)

Upstream source: https://github.com/nmrugg/stockfish.js/tree/v19.0.1
Original source archive: https://codeload.github.com/nmrugg/stockfish.js/tar.gz/refs/tags/v19.0.1
Package: https://registry.npmjs.org/stockfish/-/stockfish-19.0.1.tgz
Network source (also identified by the upstream source's scripts/net.sh):
https://tests.stockfishchess.org/api/nn/nn-61e7af4bb97d.nnue

The archive includes engine source, build scripts, option handling and net-fetch
instructions. With the upstream documented toolchain (Emscripten 6.0.9), place the
supplied NNUE file in `src/`, then use `node build.js --single-threaded --lite`.
Consult the archived README/build.js/Makefile for exact build prerequisites.
The application distributes the registry engine unchanged; it does not claim an
independent bit-for-bit rebuild. App-specific UCI controller code is in the app's
`web/src/lib/browser-stockfish*.ts` and `browser-engine-lease.ts`.

Redistributors must retain engine attribution/license and provide the complete
corresponding source, including the supplied source archive and build input.
Do not publish only the JS/WASM pair while removing these source materials.
This notice does not relicense the entire application.

## Resource controls

This is the single-threaded lite build, not the installed native Stockfish.
The app requests Hash=16 MiB (or a smaller advertised maximum), Threads=1 if
advertised, MultiPV=3 if supported, and a search ending at depth 14 or 10 seconds.
The unmodified WASM declares 128 MiB initial linear memory and 2 GiB maximum.
These are not browser RSS measurements or an exact aggregate memory guarantee.
The backend's independent 512 MiB policy is unchanged.
