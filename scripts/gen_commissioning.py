#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""读取 config/device.yaml(及 device.local.yaml 覆盖),生成:

  out/factory_partition.bin   factory 分区镜像(配对信息、设备标识、证书)
  out/onboarding_codes.csv    QR 配对串与手动配对码
  firmware/sdkconfig.device   固件编译选项片段(调试开关、VID/PID 等,不提交)

factory 分区镜像调用 SDK 的 generate_esp32_chip_factory_bin.py 生成,
QR 与手动配对码由 SDK 的 SetupPayload 计算。

用法: .venv/bin/python scripts/gen_commissioning.py
"""
import argparse
import csv
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SDK = ROOT / ".deps/connectedhomeip"
SDK_FACTORY_TOOL = SDK / "scripts/tools/generate_esp32_chip_factory_bin.py"

# Matter 规范禁用的 passcode
INVALID_PASSCODES = {0, 12345678, 87654321} | {int(str(d) * 8) for d in range(1, 10)}

# 默认配置下必须得到的已知结果,用来发现生成流程出了问题
# (VID, PID, discriminator, passcode) -> (QR, 手动配对码);与改用 factory 分区之前的固件日志一致
KNOWN_VECTORS = {
    (0xFFF1, 0x8000, 3840, 20202021): ("MT:Y.K9042C00KA0648G00", "34970112332"),
}

STR_FIELDS = {  # 字段: (最小长度, 最大长度)
    "vendor_name": (1, 32),
    "product_name": (1, 32),
    "serial_number": (1, 32),
    "hardware_version_string": (1, 64),
}
ATTESTATION_FILES = ("dac_cert", "dac_key", "pai_cert", "cd")
TOP_KEYS = {"vendor_id", "product_id", "hardware_version", "discriminator", "passcode", "debug_shell", "attestation",
            *STR_FIELDS}


def rel(p: Path) -> Path:
    try:
        return p.relative_to(ROOT)
    except ValueError:
        return p


def die(msg):
    print(f"错误:{msg}", file=sys.stderr)
    sys.exit(1)


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        die(f"{path} 的顶层必须是键值对")
    return data


def merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(config: Path, local: Path) -> dict:
    cfg = load_yaml(config)
    if local.exists():
        cfg = merge(cfg, load_yaml(local))
        print(f"已应用本机覆盖:{rel(local)}")
    return cfg


def int_in(cfg, key, lo, hi):
    v = cfg.get(key)
    if not isinstance(v, int) or isinstance(v, bool):
        die(f"{key} 必须是整数,当前为 {v!r}")
    if not lo <= v <= hi:
        die(f"{key} 超出范围 [{lo:#x}, {hi:#x}],当前为 {v:#x}")
    return v


def validate(cfg: dict) -> dict:
    unknown = set(cfg) - TOP_KEYS
    if unknown:
        die(f"未知配置项:{', '.join(sorted(unknown))}(检查拼写)")
    missing = TOP_KEYS - set(cfg)
    if missing:
        die(f"缺少配置项:{', '.join(sorted(missing))}")

    v = {
        "vendor_id": int_in(cfg, "vendor_id", 1, 0xFFFE),
        "product_id": int_in(cfg, "product_id", 1, 0xFFFE),
        "hardware_version": int_in(cfg, "hardware_version", 0, 0xFFFF),
        "discriminator": int_in(cfg, "discriminator", 0, 0xFFF),
        "passcode": int_in(cfg, "passcode", 1, 99999998),
    }
    if v["passcode"] in INVALID_PASSCODES:
        die(f"passcode {v['passcode']} 是规范禁用值")
    for key, (lo, hi) in STR_FIELDS.items():
        s = cfg[key]
        if not isinstance(s, str) or not lo <= len(s) <= hi:
            die(f"{key} 必须是 {lo}~{hi} 个字符的字符串,当前为 {s!r}")
        v[key] = s
    if not isinstance(cfg["debug_shell"], bool):
        die(f"debug_shell 必须是 true 或 false,当前为 {cfg['debug_shell']!r}")
    v["debug_shell"] = cfg["debug_shell"]

    att = cfg["attestation"]
    if not isinstance(att, dict):
        die("attestation 必须是键值对")
    mode = att.get("mode")
    if mode == "custom":
        die("attestation.mode: custom(自生成证书)尚未实现,目前只支持 test")
    if mode != "test":
        die(f"attestation.mode 必须是 test 或 custom,当前为 {mode!r}")
    v["attestation"] = {}
    for key in ATTESTATION_FILES:
        if key not in att:
            die(f"attestation 缺少 {key}")
        p = (ROOT / att[key]).resolve()
        if not p.is_file():
            hint = ",请先运行 scripts/extract_test_attestation.py" if "attestation/test" in str(att[key]) else ""
            die(f"找不到 attestation.{key}:{att[key]}{hint}")
        v["attestation"][key] = p
    return v


def run_factory_tool(v: dict, workdir: Path) -> tuple[Path, str, str]:
    """调用 SDK 脚本。它会在当前目录写临时的 DAC 私钥文件,所以在 workdir 中运行。"""
    outdir = workdir / "factory"
    a = v["attestation"]
    cmd = [
        sys.executable, str(SDK_FACTORY_TOOL),
        "-p", str(v["passcode"]), "-d", str(v["discriminator"]),
        "--dac-cert", str(a["dac_cert"]), "--dac-key", str(a["dac_key"]),
        "--pai-cert", str(a["pai_cert"]), "--cd", str(a["cd"]),
        "--vendor-id", str(v["vendor_id"]), "--vendor-name", v["vendor_name"],
        "--product-id", str(v["product_id"]), "--product-name", v["product_name"],
        "--hw-ver", str(v["hardware_version"]), "--hw-ver-str", v["hardware_version_string"],
        "--serial-num", v["serial_number"],
        "--target", "esp32s3", "--size", "0x6000", "--output_dir", str(outdir),
    ]
    r = subprocess.run(cmd, cwd=workdir, capture_output=True, text=True)
    if r.returncode != 0:
        sys.stderr.write(r.stdout + r.stderr)
        die("SDK 的 factory 分区生成脚本失败")
    with open(outdir / "onboarding_codes.csv") as f:
        row = next(csv.DictReader(f))
    return outdir / "factory_partition.bin", row["qrcode"], row["manualcode"]


def sdkconfig_fragment(v: dict) -> str:
    return (
        "# 由 scripts/gen_commissioning.py 根据 config/ 生成,请勿手改,也不要提交。\n"
        f"CONFIG_ENABLE_CHIP_SHELL={'y' if v['debug_shell'] else 'n'}\n"
        f"CONFIG_DEVICE_VENDOR_ID={v['vendor_id']:#x}\n"
        f"CONFIG_DEVICE_PRODUCT_ID={v['product_id']:#x}\n"
        f"CONFIG_DEFAULT_DEVICE_HARDWARE_VERSION={v['hardware_version']}\n"
    )


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.write_text(text)
    return True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=ROOT / "config/device.yaml")
    ap.add_argument("--local", type=Path, default=ROOT / "config/device.local.yaml")
    ap.add_argument("--out", type=Path, default=ROOT / "out")
    ap.add_argument("--sdkconfig", type=Path, default=ROOT / "firmware/sdkconfig.device")
    args = ap.parse_args()

    if not SDK_FACTORY_TOOL.is_file():
        die("找不到 SDK,请先运行 scripts/setup.sh")

    v = validate(load_config(args.config, args.local))

    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.out, prefix=".work-") as tmp:
        image, qr, manual = run_factory_tool(v, Path(tmp))

        key = (v["vendor_id"], v["product_id"], v["discriminator"], v["passcode"])
        if key in KNOWN_VECTORS and KNOWN_VECTORS[key] != (qr, manual):
            die(f"配对码自检失败:期望 {KNOWN_VECTORS[key]},实际 {(qr, manual)}")

        shutil.copyfile(image, args.out / "factory_partition.bin")
    with open(args.out / "onboarding_codes.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["qrcode", "manualcode"])
        w.writerow([qr, manual])

    changed = write_if_changed(args.sdkconfig, sdkconfig_fragment(v))

    print(f"VID/PID        {v['vendor_id']:#06x} / {v['product_id']:#06x}")
    print(f"discriminator  {v['discriminator']}    passcode {v['passcode']}")
    print(f"QR             {qr}")
    print(f"手动配对码     {manual}")
    print(f"调试 shell     {'开' if v['debug_shell'] else '关'}")
    print(f"证书           test({rel(v['attestation']['dac_cert'].parent)})")
    print(f"已生成 {rel(args.out)}/factory_partition.bin"
          f"、{rel(args.sdkconfig)}({'有变化' if changed else '无变化'})")


if __name__ == "__main__":
    main()
