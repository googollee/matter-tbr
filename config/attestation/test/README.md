# 测试证书(Matter Dev 链)

由 `scripts/extract_test_attestation.py` 生成,请勿手改。

这是 Matter SDK 示例固件(`GetExampleDACProvider()`)内置的测试证书,
VID `0xFFF1` / PID `0x8000`。这些材料在 SDK 中是公开的,**仅限开发测试**,
所有使用它的设备证书都相同,不能用于量产或对外分发。

| 文件 | 内容 | 来源(SDK `src/credentials/examples/`) |
| --- | --- | --- |
| `dac_cert.der` | DAC 证书 | `ExampleDACs.cpp` `kDevelopmentDAC_Cert_FFF1_8000` |
| `dac_key.der` | DAC 私钥(PKCS#8 DER,由裸 32 字节私钥包装) | `ExampleDACs.cpp` `kDevelopmentDAC_PrivateKey_FFF1_8000` |
| `pai_cert.der` | PAI 证书 | `ExamplePAI.cpp` `kDevelopmentPAI_Cert_FFF1` |
| `cd.der` | Certification Declaration | `DeviceAttestationCredsExample.cpp` `kCdForAllExamples`(VID=0xFFF1 回退分支) |

- SDK 提交:`d305b0761957c960c26c0841e1b50add2365871d`
- DAC subject:`1.3.6.1.4.1.37244.2.2=8000,1.3.6.1.4.1.37244.2.1=FFF1,CN=Matter Dev DAC 0xFFF1/0x8000`
- PAI subject:`1.3.6.1.4.1.37244.2.1=FFF1,CN=Matter Dev PAI 0xFFF1 no PID`

提取时已离线校验:DAC 私钥与公钥匹配、DAC 由该 PAI 签发、CD 含 VID/PID。
这条链与 SDK `credentials/test/attestation/` 目录里的 "Matter Test" 链**不是同一套**。
