"""Write N synthetic supplier feeds in the formats real suppliers send.

    python scripts/make_feeds.py --count 200 --out feeds/

Fixed seed: the same command writes byte-identical files every time. Every
supplier, product and URL is invented; images live on example.com.
"""

import argparse
import csv
import hashlib
import io
import random
from pathlib import Path

SEED = 20261001

# (headers for sku, title, variant, price, rrp, qty, vendor, image, barcode), delimiter,
# decimal separator, encoding: the four shapes of export these feeds come in.
FORMATS = [
    (("SKU", "Title", "Variant", "Price", "RRP", "Stock", "Brand", "Image URL", "EAN"), ",", ".", "utf-8"),
    (("Art-Nr", "Bezeichnung", "Variante", "Preis EUR", "UVP", "Bestand", "Hersteller", "Bild-URL", "EAN"),
     ";", ",", "utf-8-sig"),
    (("Item Code", "Product Name", "Size", "Net Price", "MSRP", "Qty", "Manufacturer", "Picture", "GTIN"),
     "\t", ".", "utf-8"),
    (("Kod", "Nazwa", "Color", "Cena", "Recommended", "Ilosc", "Marka", "Photo", "Barcode"), "|", ",", "cp1252"),
]
NOUNS = ["Thermometer", "Knife", "Cutting Board", "Grinder", "Scale", "Timer", "Whisk", "Ladle", "Peeler", "Tongs"]
ADJECTIVES = ["Precision", "Compact", "Digital", "Steel", "Bamboo", "Pro", "Classic", "Travel", "Folding", "Heavy"]
VARIANTS = ["Red", "Blue", "Green", "Black", "S", "M", "L"]
VENDORS = ["Northwind Tools", "Acme Kitchen", "Globex Home", "Initech Supply"]


def money(value: float, decimal: str) -> str:
    text = f"{value:.2f}"
    return text.replace(".", ",") if decimal == "," else text


def feed(index: int, rng: random.Random) -> tuple[str, bytes]:
    headers, delimiter, decimal, encoding = FORMATS[index % len(FORMATS)]
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\n")
    writer.writerow(headers)
    for product in range(rng.randint(15, 60)):
        sku = f"F{index:03d}-{product:03d}"
        title = f"{rng.choice(ADJECTIVES)} {rng.choice(NOUNS)} {product}"
        if encoding == "cp1252":
            title += " Caf" + chr(233)  # a Windows export with an accented letter
        price = round(rng.uniform(3, 250), 2)
        variants = rng.sample(VARIANTS, rng.choice([1, 1, 1, 2, 3]))
        for v, variant in enumerate(variants):
            row = [sku, title, variant if len(variants) > 1 else "", money(price, decimal),
                   money(price * rng.uniform(1.1, 1.8), decimal), str(rng.randint(0, 40)),
                   rng.choice(VENDORS), f"https://example.com/img/{sku.lower()}-{v}.jpg",
                   f"400{index:03d}{product:03d}{v:03d}"]
            # The defects real feeds carry, at roughly real rates.
            roll = rng.random()
            if roll < 0.03:
                row[0] = ""                      # no SKU: blocks the row
            elif roll < 0.06:
                row[3] = money(0, decimal)        # zero price: blocks the row
            elif roll < 0.10:
                row[5] = ""                      # stock left blank
            elif roll < 0.13:
                row[5] = str(-rng.randint(1, 5))  # negative stock
            elif roll < 0.18:
                row[7] = ""                      # no image
            elif roll < 0.21:
                row[7] = row[7].replace("https://", "http://")
            elif roll < 0.25:
                row[8] = ""                      # no barcode
            writer.writerow(row)
    return f"feed-{index:03d}.csv", buffer.getvalue().encode(encoding)


def make(count: int, out: Path) -> list[Path]:
    rng = random.Random(SEED)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for index in range(count):
        name, data = feed(index, rng)
        (out / name).write_bytes(data)
        written.append(out / name)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=200)
    parser.add_argument("--out", type=Path, default=Path("feeds"))
    args = parser.parse_args()
    files = make(args.count, args.out)
    digest = hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()
    print(f"{len(files)} feeds written to {args.out}/, {sum(p.stat().st_size for p in files):,} bytes, "
          f"sha256 of all {digest}")


if __name__ == "__main__":
    main()
