#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Googol Lee
"""从 Matter SDK 源码提取示例固件内置的测试证书(Matter Dev 链),写入 config/attestation/test/。

SDK 的 GetExampleDACProvider() 把 DAC / PAI / CD 编译成字节数组,而不是文件。
切换到 factory 分区后需要这些数据以文件形式提供,且必须与现有固件一致,
这样设备的证书链不变,已有的配对不受影响。

提取后做离线校验:DAC 私钥与公钥匹配、DAC 由该 PAI 签发、CD 含 VID/PID。
同一个 SDK 版本重复运行,输出逐字节相同(README 里不写时间戳)。

用法: .venv/bin/python scripts/extract_test_attestation.py [--sdk DIR] [--out DIR]
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

ROOT = Path(__file__).resolve().parent.parent
VID, PID = 0xFFF1, 0x8000


def c_array(source: str, name: str) -> bytes:
    """取出 `name[N] = { 0x.., ... };` 的字节内容,并核对声明的长度。"""
    m = re.search(r"\b" + re.escape(name) + r"\[(\d*)\]\s*=\s*\{(.*?)\};", source, re.S)
    if not m:
        raise SystemExit(f"错误:源码中找不到数组 {name}")
    data = bytes(int(x, 16) for x in re.findall(r"0x([0-9a-fA-F]{2})", m.group(2)))
    if m.group(1) and int(m.group(1)) != len(data):
        raise SystemExit(f"错误:{name} 声明长度 {m.group(1)} 与实际 {len(data)} 不符")
    return data


def extract(sdk: Path):
    ex = sdk / "src/credentials/examples"
    dacs = (ex / "ExampleDACs.cpp").read_text()
    pai = (ex / "ExamplePAI.cpp").read_text()
    cds = (ex / "DeviceAttestationCredsExample.cpp").read_text()

    tag = f"FFF1_{PID:04X}"
    dac_cert = c_array(dacs, f"kDevelopmentDAC_Cert_{tag}")
    dac_pub = c_array(dacs, f"kDevelopmentDAC_PublicKey_{tag}")
    dac_priv = c_array(dacs, f"kDevelopmentDAC_PrivateKey_{tag}")
    pai_cert = c_array(pai, "kDevelopmentPAI_Cert_FFF1")

    # VID=0xFFF1 的 CD 在 #else 回退分支里
    marker = "#else  /* Fall back to the VID=0xFFF1 CD */"
    if marker not in cds:
        raise SystemExit("错误:DeviceAttestationCredsExample.cpp 的结构与预期不符(找不到 FFF1 回退分支)")
    cd = c_array(cds.split(marker, 1)[1], "kCdForAllExamples")
    return dac_cert, dac_pub, dac_priv, pai_cert, cd


def verify(dac_cert, dac_pub, dac_priv, pai_cert, cd):
    key = ec.derive_private_key(int.from_bytes(dac_priv, "big"), ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    assert pub == dac_pub, "DAC 私钥与内置公钥不匹配"

    dac = x509.load_der_x509_certificate(dac_cert)
    pai = x509.load_der_x509_certificate(pai_cert)
    dac_cert_pub = dac.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    assert dac_cert_pub == dac_pub, "DAC 证书里的公钥与内置公钥不匹配"
    dac.verify_directly_issued_by(pai)

    assert cd[:1] == b"\x30" and b"\x2a\x86\x48\x86\xf7\x0d\x01\x07\x02" in cd, "CD 不是 CMS SignedData"
    assert VID.to_bytes(2, "little") in cd, "CD 中没有 VID"
    assert PID.to_bytes(2, "little") in cd, "CD 中没有 PID"
    return key, dac, pai


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sdk", type=Path, default=ROOT / ".deps/connectedhomeip")
    ap.add_argument("--out", type=Path, default=ROOT / "config/attestation/test")
    args = ap.parse_args()

    dac_cert, dac_pub, dac_priv, pai_cert, cd = extract(args.sdk)
    key, dac, pai = verify(dac_cert, dac_pub, dac_priv, pai_cert, cd)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "dac_cert.der").write_bytes(dac_cert)
    (args.out / "pai_cert.der").write_bytes(pai_cert)
    (args.out / "cd.der").write_bytes(cd)
    (args.out / "dac_key.der").write_bytes(
        key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )

    commit = subprocess.run(["git", "-C", str(args.sdk), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (args.out / "README.md").write_text(
        f"""# 测试证书(Matter Dev 链)

由 `scripts/extract_test_attestation.py` 生成,请勿手改。

这是 Matter SDK 示例固件(`GetExampleDACProvider()`)内置的测试证书,
VID `0x{VID:04X}` / PID `0x{PID:04X}`。这些材料在 SDK 中是公开的,**仅限开发测试**,
所有使用它的设备证书都相同,不能用于量产或对外分发。

| 文件 | 内容 | 来源(SDK `src/credentials/examples/`) |
| --- | --- | --- |
| `dac_cert.der` | DAC 证书 | `ExampleDACs.cpp` `kDevelopmentDAC_Cert_FFF1_{PID:04X}` |
| `dac_key.der` | DAC 私钥(PKCS#8 DER,由裸 32 字节私钥包装) | `ExampleDACs.cpp` `kDevelopmentDAC_PrivateKey_FFF1_{PID:04X}` |
| `pai_cert.der` | PAI 证书 | `ExamplePAI.cpp` `kDevelopmentPAI_Cert_FFF1` |
| `cd.der` | Certification Declaration | `DeviceAttestationCredsExample.cpp` `kCdForAllExamples`(VID=0xFFF1 回退分支) |

- SDK 提交:`{commit}`
- DAC subject:`{dac.subject.rfc4514_string()}`
- PAI subject:`{pai.subject.rfc4514_string()}`

提取时已离线校验:DAC 私钥与公钥匹配、DAC 由该 PAI 签发、CD 含 VID/PID。
这条链与 SDK `credentials/test/attestation/` 目录里的 "Matter Test" 链**不是同一套**。
"""
    )

    print(f"已写入 {args.out}")
    print(f"  DAC: {dac.subject.rfc4514_string()}")
    print(f"  PAI: {pai.subject.rfc4514_string()}")
    print("  校验通过:密钥匹配、DAC 由 PAI 签发、CD 含 VID/PID")


if __name__ == "__main__":
    sys.exit(main())
