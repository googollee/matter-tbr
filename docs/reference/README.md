# 本地参考资料

此目录除本文件外不会提交到仓库,仅保存在本机。

需要放入的文件:
- `esp-thread-br-schematic-v1.2.pdf`:ESP Thread Border Router v1.2 原理图(Espressif 提供)。官方下载页(<https://docs.espressif.com/projects/esp-thread-br/en/latest/hardware_platforms.html>)现在只提供 v1.3:<https://dl.espressif.com/dl/schematics/esp_thread_br_zigbee_gw_schematics_v1.3.pdf>;v1.2 已没有公开链接(`..._v1.2.pdf` 返回 404),本地这份是早先留存的。v1.3 与 v1.2 有改动(多了设计框图页等),J4 排针的信号分配未逐项核对,以板子丝印为准
- `esp-thread-br-board-v1.step`:板子 3D 模型(STEP,2024-04 版),设计外壳时量尺寸用。来源:<https://dl.espressif.com/dl/schematics/ESP-Thread%20BR&Zigbee%20GW_V1.zip>(zip 里的 `ESP-Thread BR&Zigbee GW_V1-202404.STEP`)
- `esp-thread-br-board-front.webp`:板子正面实物图,带元件标注。来源:<https://docs.espressif.com/projects/esp-thread-br/en/latest/hardware_platforms.html>

量得的关键尺寸:PCB 51 × 51 mm(含天线 51.4 × 62.6);J4 排针 2×13、间距 2.54,列中心 x = 2.77 / 5.31,行中心 y = 2.59 + 2.54k(原点为板子左下角,H2 在上、USB 在右);无安装孔。STEP 里看不出 PCB 厚度,需实测。
