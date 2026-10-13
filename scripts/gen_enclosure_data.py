#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""读取 config/device.yaml(及 device.local.yaml 覆盖),为外壳生成配对信息:

  --defines  打印传给 OpenSCAD 的 -D 参数(passcode / discriminator / VID / PID)

外壳 enclosure/enclosure.scad 自己用这四个值算出 QR 和手动配对码,印在底壳底面。
和 gen_commissioning.py 不同,这里不需要 SDK。

用法:
  .venv/bin/python scripts/gen_enclosure_data.py --defines       打印 -D 参数,同时把摘要写到 stderr
  .venv/bin/python scripts/gen_enclosure_data.py --verify LOG    比对 OpenSCAD 日志里算出的 QR 和手动配对码
                                                                 与 out/onboarding_codes.csv(由 make config 生成)
"""
import argparse
import contextlib
import csv
import re
import sys
from pathlib import Path

import gen_commissioning as gc

ROOT = gc.ROOT


def scad_defines(v: dict) -> str:
    return (f"-D qr_passcode={v['passcode']} -D qr_discriminator={v['discriminator']} "
            f"-D qr_vid={v['vendor_id']} -D qr_pid={v['product_id']}")


def echo_value(log: str, name: str) -> str:
    m = re.search(rf'ECHO: {name} = "([^"]*)"', log)
    if not m:
        gc.die(f"OpenSCAD 日志里没有 {name},外壳是否改过 echo?")
    return m.group(1)


def verify(v: dict, log_path: Path, codes: Path):
    log = log_path.read_text()
    qr, manual = echo_value(log, "payload"), echo_value(log, "manual_code")
    key = (v["vendor_id"], v["product_id"], v["discriminator"], v["passcode"])

    if codes.is_file():
        with open(codes) as f:
            row = next(csv.DictReader(f))
        expected, src = (row["qrcode"], row["manualcode"]), gc.rel(codes)
    elif key in gc.KNOWN_VECTORS:
        expected, src = gc.KNOWN_VECTORS[key], "已知的默认值"
    else:
        print(f"QR {qr}  手动配对码 {manual}(没有 {gc.rel(codes)} 可比对,先运行 make config 可以校验)")
        return

    if expected != (qr, manual):
        gc.die(f"外壳算出的配对码与{src}不一致:外壳 {(qr, manual)},期望 {expected}。"
               "如果刚改过 config/,先运行 make config 更新 out/onboarding_codes.csv;否则是外壳里的 QR 实现有问题")
    print(f"QR {qr}  手动配对码 {manual}(与{src}一致)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=ROOT / "config/device.yaml")
    ap.add_argument("--local", type=Path, default=ROOT / "config/device.local.yaml")
    ap.add_argument("--codes", type=Path, default=ROOT / "out/onboarding_codes.csv")
    ap.add_argument("--defines", action="store_true", help="打印传给 OpenSCAD 的 -D 参数")
    ap.add_argument("--verify", type=Path, metavar="LOG", help="比对 OpenSCAD 日志里算出的配对码")
    args = ap.parse_args()

    # load_config 会向 stdout 打印"已应用本机覆盖",--defines 的 stdout 要留给 -D 参数
    with contextlib.redirect_stdout(sys.stderr if args.defines else sys.stdout):
        v = gc.validate(gc.load_config(args.config, args.local))

    if args.verify:
        verify(v, args.verify, args.codes)
        return

    if not args.defines:
        ap.error("需要 --defines 或 --verify")
    print(f"VID/PID        {v['vendor_id']:#06x} / {v['product_id']:#06x}", file=sys.stderr)
    print(f"discriminator  {v['discriminator']}    passcode {v['passcode']}", file=sys.stderr)
    print(scad_defines(v))


if __name__ == "__main__":
    main()
