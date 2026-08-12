# penplan

[![ci](https://github.com/joyboyisalive07-lab/penplan/actions/workflows/ci.yml/badge.svg)](https://github.com/joyboyisalive07-lab/penplan/actions/workflows/ci.yml)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Воспроизводит изображение на браузерном холсте для рисования, двигая настоящую
мышь, и планирует штрихи так, чтобы рисунок был закончен за заданное вами время.

Инструмент ничего не знает про конкретный сайт. Он изучает холст по профилю
калибровки: границы холста, реальные цвета палитры (считанные с экрана, а не
записанные с ваших слов), положение кисти и заливки, доступные размеры кисти.
Gartic Phone, skribbl.io, drawaria.online и Windows Paint для него одна и та же
задача.

Он видит экран и двигает мышь. Он не внедряет скрипты в страницу, не трогает
сетевой трафик сайта, не управляет аккаунтом и вообще не делает сетевых
запросов.

English: [README.md](README.md).

## Состояние

В работе. Этот раздел будет заменён описанием использования, калибровки и
измеренными числами планировщика до того, как будет поставлен тег 1.0.0.

## Сборка из исходников

```
git clone https://github.com/joyboyisalive07-lab/penplan
cd penplan
pip install -e ".[dev]"
pytest
```

## Документация

- [docs/ALGORITHM.md](docs/ALGORITHM.md): квантование, проверка заливок, выбор
  размера кисти, оптимизация маршрута, модель стоимости.
- [docs/DECISIONS.md](docs/DECISIONS.md): каждое неочевидное решение и его
  причина.

## Лицензия

MIT, авторские права joyboyisalive07-lab. См. [LICENSE](LICENSE).
