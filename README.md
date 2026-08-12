# penplan

[![ci](https://github.com/joyboyisalive07-lab/penplan/actions/workflows/ci.yml/badge.svg)](https://github.com/joyboyisalive07-lab/penplan/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Reproduces an image on a browser drawing canvas by moving the real mouse, and
plans the strokes so the drawing finishes inside a time budget you set.

The tool knows nothing about any particular site. It learns a canvas from a
calibration profile: where the canvas is, what colours the palette actually
contains (read from the screen, not declared), where the brush and fill tools
are, and which brush sizes exist. Gartic Phone, skribbl.io, drawaria.online and
Windows Paint are the same problem to it.

It sees the screen and moves the mouse. It injects no scripts, touches no
network traffic, automates no account, and makes no network requests at all.

Russian: [README.ru.md](README.ru.md).

## Status

Under construction. This section is replaced by usage, calibration and
measured planner numbers before 1.0.0 is tagged.

## Building from source

```
git clone https://github.com/joyboyisalive07-lab/penplan
cd penplan
pip install -e ".[dev]"
pytest
```

## Documentation

- [docs/ALGORITHM.md](docs/ALGORITHM.md): quantization, fill verification,
  brush sizing, tour optimization, cost model.
- [docs/DECISIONS.md](docs/DECISIONS.md): every non-obvious choice and why.

## License

MIT, copyright joyboyisalive07-lab. See [LICENSE](LICENSE).
