// matter-tbr 外壳：ESP Thread Border Router v1.2 开发板 + 外接 WS2812 状态灯
//
// 两件打印：
//   底壳 base  开发板嵌入其中，底面凹刻 Matter 配对二维码和手动配对码
//   上盖 lid   顶面完全平整；WS2812 模块嵌在盖子里，LED 完全镂空；
//              模块靠两根立柱穿过它的安装孔，M2 自攻螺丝 + 垫圈从背面拧紧固定（不靠胶）；
//              盖子上有两个按键，各是一根长导杆，直接顶到 S3 的 BOOT / RESET 键。
//              导杆和盖子一起打印，靠 3 根很细的断点柱固定在滑动孔里，
//              装机前用力按一下断点柱就断开，导杆变成能滑动的活动件。
// 合并后用 4 颗 M3 沉头螺丝固定：螺丝从底壳底面穿入，拧进上盖的立柱（自攻，预留 2.6 mm 孔）。
// 螺丝长度建议 M3 x 10。
//
// 用法：改 part 选择输出。
//   "assembly"  整机（含开发板、LED 模块的示意体，看装配用）
//   "exploded"  爆炸图
//   "base"      底壳，按打印姿态（底面朝下贴热床）
//   "lid"       上盖，按打印姿态（翻面：外表面朝下贴热床，无需支撑；导杆已在孔里）
//   "check_base" / "check_lid" / "check_fit"  干涉检查，渲染结果应为空
//
// 坐标：以开发板左下角（PCB 角）为原点，x 向右，y 向上（H2 模块在上，USB 在右），
// z 向上，z = 0 是底壳外底面。开发板的尺寸来自官方 STEP 模型
// （docs/reference/esp-thread-br-board-v1.step），尺寸与推导见 board/README.md。
// 标注“估计”的参数没有来源数据，需要实测后修正。

part = "assembly";

// 配对信息：默认是 SDK 测试值（QR MT:Y.K9042C00KA0648G00，手动配对码 3497-011-2332）。
// make enclosure 会根据 config/ 用 -D 覆盖这四个值。
qr_passcode      = 20202021;
qr_discriminator = 3840;
qr_vid           = 65521;   // 0xFFF1
qr_pid           = 32768;   // 0x8000

$fn = 48;
eps = 0.01;

/* ---------- 开发板 ---------- */

pcb_w = 51;          // PCB 尺寸
pcb_h = 51;
pcb_t = 1.6;         // 估计：STEP 模型里没有板厚，需实测
ant_s3_down = 6.28;  // S3 天线伸出 PCB 下边
ant_h2_up   = 5.36;  // H2 模块（含天线区）伸出 PCB 上边

// 以下高度均相对 PCB 顶面（从 STEP 读出）
h_s3_module = 3.35;  // S3 模块屏蔽罩
h_h2_module = 2.8;   // H2 模块
h_usb       = 3.41;  // USB-C 顶面
usb_below   = 0.55;  // USB-C 外壳比 PCB 顶面低的部分
h_key       = 3.05;  // S3 侧按键（BOOT2 / RESET2）顶面
h_j5_pin    = 8.62;  // J5 排针（H2 调试口）针顶
h_j4_body   = 2.54;  // J4 塑料座
pin_tail    = 2.54;  // J4 / J5 针从塑料座底面向下伸出的长度（穿过 PCB 后，伸出 PCB 底面 = pin_tail - pcb_t）

usb_y = [14.0, 32.0];             // 两个 USB-C 中心的 y（S3 口、H2 口）
usb_w = 8.94;                     // USB-C 宽度
key_xy = [[38.0, 3.0], [47.0, 3.0]];   // BOOT2、RESET2 中心
key_names = ["B", "R"];                  // 刻在盖子上：B = BOOT，R = RESET（整词放不下，笔画也太细）
j5_box = [18.79, 40.49, 27.3, 43.92];  // J5 排针 x0 y0 x1 y1

/* ---------- 外接 LED：2x3 排母 + WS2812 模块 ---------- */

socket_box = [1.5, 26.72, 6.58, 34.34];   // 2x3 排母占位 x0 y0 x1 y1（插在 J4 的 1-6 脚）
socket_h   = 8.5;     // 估计：排母塑料座高度
socket_tail = 3.0;    // 估计：焊线用的尾针长度

mod_w = 17.78;        // WS2812 模块尺寸（SparkFun BOB-13282，型号未定，按实物改）
mod_d = 12.7;
mod_t = 1.6;
mod_pins = 2.5;       // 估计：模块背面焊点和引脚凸出的高度
mod_cx = 17.0;        // 模块中心（板坐标）
mod_cy = 30.5;
mod_clr = 0.3;        // 模块凹槽单边间隙
mod_pocket = mod_t + 0.2;   // 凹槽深度：模块完全沉进去，背面比凹槽口低 0.2
// 以下数据来自 SparkFun WS2812_Breakout V_1.1b 的 Eagle 板文件：板 17.78 x 12.7，
// 原点在左下角，LED 在 (8.89, 4.22)，安装孔在 (2.54, 10.16) 和 (15.24, 10.16)。
// 方向：从 LED 面看，输入排针在左（朝向 J4 排母），LED 偏下，安装孔在上。
led_dx = 0;           // LED 相对模块中心的偏移
led_dy = 4.2164 - mod_d / 2;
mod_hole_dx = 6.35;   // 两个安装孔相对模块中心：x = ±mod_hole_dx，y = +mod_hole_dy
mod_hole_dy = 10.16 - mod_d / 2;
mod_hole_d = 3.302;   // 孔径（M3 尺寸，M2 螺丝穿不住，靠立柱和垫圈固定）
mod_pin_x = 1.27 - mod_w / 2;   // 输入排针（3 针，y 从模块底边起 1.27 / 3.81 / 6.35）相对模块中心的 x
mod_pin_y0 = 1.27 - mod_d / 2;  // 第一根针
// 固定：上盖上长出立柱穿过安装孔，M2 自攻螺丝 + M2 垫圈（外径 5）从背面拧进立柱，压住模块
post_d = 3.0;         // 立柱直径，比孔小 0.3
post_tip = 0.2;       // 立柱尖比模块背面高多少，保证垫圈压在模块上，不是压在立柱上
m2_pilot_d = 1.7;     // M2 自攻孔，PLA / PETG 通用，实测后调
m2_pilot_depth = 3.0; // 自攻孔深度（从立柱尖算起），螺丝咬合长度约 3 mm，建议 M2 x 6
led_pkg = 5.0;        // 估计：5050 封装边长
led_h = 1.6;
led_win = 5.8;        // LED 窗口边长（完全镂空）

/* ---------- 外壳 ---------- */

W = 2.4;              // 壁厚
rc = 1.5;             // 腔体内圆角
cl = 0.4;             // 板子与腔体侧壁的间隙
F = 2.0;              // 底板厚度，等于二维码板厚度
under = 2.0;          // PCB 底面离底板顶面的距离（排针尾针伸出约 1 mm）
TR = 4.2;             // 上盖顶板厚度（要容得下导杆的圆帽和台肩；LED 模块立柱的自攻孔也在里面，顶面至少留 0.8）
lid_clear = 15.0;     // PCB 顶面到顶板内面的距离（排母尾针最高 14.04）
split_above = 1.0;    // 分型面比 PCB 顶面高多少
lip_h = 2.0;          // 止口高度
tc = 0.2;             // 止口间隙

// 腔体范围（板坐标）：包住 PCB 和两端的天线
cav_x0 = -cl;
cav_x1 = pcb_w + cl;
cav_y0 = -ant_s3_down - 0.5;
cav_y1 = pcb_h + ant_h2_up + 0.5;

// 高度基准
zb      = F + under + pcb_t;        // PCB 顶面
z_split = zb + split_above;         // 分型面
z_hi_in = zb + lid_clear;            // 顶板内面
z_hi_top = z_hi_in + TR;             // 顶板外面（上盖顶面，整面平整）

// 螺丝：x, y, 靠哪面墙（-1 下 / +1 上）。位置都在天线伸出的空角里，不占 PCB。
screws = [[3.0, -3.5, -1], [30.6, -3.5, -1], [22.0, 54.5, 1], [47.0, 54.5, 1]];
boss_r = 3.2;
screw_d = 3.4;        // 底壳过孔
csk_d = 6.8;          // 沉头直径
csk_h = (csk_d - screw_d) / 2;
pilot_d = 2.6;        // 上盖自攻孔
pilot_depth = 4.2;

// PCB 底面的支撑垫：x0 y0 x1 y1（避开 J4 / J5 的针脚）
pads = [[0.4, 0.4, 12, 2.0], [0.4, 36, 2.0, 50.6], [34, 0.4, 50.6, 5.8],
        [46.8, 5.8, 50.6, 40.0], [30, 47.5, 50.6, 50.6]];

// USB-C 开口：比插头外壳大，让插头头部能伸进墙里
usb_open_w = 14;
usb_open_h = 8;

// 导杆按键（上盖顶板上，打印在孔里）
// 导杆从上到下：圆帽（和顶面齐平，手指按这里）-> 杆 -> 过导向管 -> 圆环（防止杆从孔里抽出）-> 杆尖（顶 S3 的按键）
pin_d = 3.6;          // 杆直径
pin_clr = 0.25;       // 杆和孔的单边间隙（打印后能自由滑动）
pad_r = 3.4;          // 圆帽半径
pad_t = 1.2;          // 圆帽厚度
pad_clr = 0.3;        // 圆帽和沉孔的单边间隙
btn_stroke = 0.8;     // 估计：圆帽能往下按的行程，到台肩为止。要大于 key_gap + 按键行程（约 0.25）
key_gap = 0.35;       // 杆尖与按键的间隙
tube_od = 7.4;        // 导向管外径（管从顶板内面往下伸，给杆多一段导向，防止歪斜）
col_d = 6.0;          // 圆环直径
col_t = 1.0;          // 圆环厚度
tip_len = 3.0;        // 圆环下面的杆尖长度
fuse_gap = 0.3;       // 圆环和导向管端面的间隙，三根断点柱撑在里面
fuse_d = 0.9;         // 断点柱直径。越粗越难掰断，用力按不动就调细
fuse_n = 3;
label_text_size = 3.2;
label_text_depth = 0.4;

// 二维码板（底壳底面）。参数含义见下面 matter_label_plate。
lbl_m = 1.5;
lbl_text = 4;
lbl_gap = 1;
lbl_margin = 3;
lbl_iso = 1.2;
lbl_wall = 1.6;
lbl_ext = lbl_iso + lbl_wall;
lbl_w = 21 * lbl_m + 2 * lbl_margin + 2 * lbl_ext;                    // 板外形宽
lbl_h = 21 * lbl_m + lbl_gap + lbl_text + 2 * lbl_margin + 2 * lbl_ext;   // 板外形高
lbl_x = (cav_x0 + cav_x1) / 2;                                        // 板中心 x（原点是二维码水平中心）
lbl_y = (cav_y0 + cav_y1) / 2 - lbl_h / 2 + lbl_gap + lbl_text + lbl_margin + lbl_ext;  // 二维码底边的 y

/* ---------- Matter 配对信息：QR 与手动配对码，二维码板（复制自独立的测试模型） ---------- */

/* ---------- 通用位运算工具（OpenSCAD 没有位运算符） ---------- */

function sum(v) = v * [for (x = v) 1];
function concat_all(v) = [for (a = v) for (b = a) b];
function zeros(n) = [for (i = [0 : 151]) if (i < n) 0];
function bits_lsb(n, len) = [for (i = [0 : len - 1]) floor(n / pow(2, i)) % 2];
function bits_msb(n, len) = [for (i = [len - 1 : -1 : 0]) floor(n / pow(2, i)) % 2];
function xor_n(a, b, n) =
  [for (i = [0 : n - 1]) (floor(a / pow(2, i)) + floor(b / pow(2, i))) % 2]
  * [for (i = [0 : n - 1]) pow(2, i)];
function join(l, i = 0) = i >= len(l) ? "" : str(l[i], join(l, i + 1));
function pad_zero(n, w) = let(s = str(n)) len(s) >= w ? s : str(join([for (i = [1 : w - len(s)]) "0"]), s);

/* ---------- Matter：QR payload（Base38）与手动配对码 ---------- */

B38 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-.";

// 88 bit payload，字段按 LSB 在前拼接
function matter_payload_bits(passcode, disc, vid, pid, flow, rendezvous) =
  concat_all([
    bits_lsb(0, 3),          // version
    bits_lsb(vid, 16),
    bits_lsb(pid, 16),
    bits_lsb(flow, 2),       // commissioning flow
    bits_lsb(rendezvous, 8), // 发现方式：1=SoftAP 2=BLE 4=OnNetwork
    bits_lsb(disc, 12),
    bits_lsb(passcode, 27),
    bits_lsb(0, 4)           // padding
  ]);

function matter_payload_bytes(passcode, disc, vid, pid, flow, rendezvous) =
  let(b = matter_payload_bits(passcode, disc, vid, pid, flow, rendezvous))
  [for (k = [0 : 10]) [for (i = [0 : 7]) b[8 * k + i]] * [1, 2, 4, 8, 16, 32, 64, 128]];

// 把 v 编成 n 个 Base38 字符，低位在前
function b38_chunk(v, n) = join([for (i = [0 : n - 1]) B38[floor(v / pow(38, i)) % 38]]);

function matter_qr_payload(passcode, discriminator, vid = 0, pid = 0, flow = 0, rendezvous = 2) =
  assert(passcode >= 1 && passcode <= 99999998, "passcode 必须在 1 ~ 99999998")
  assert(discriminator >= 0 && discriminator <= 4095, "discriminator 必须在 0 ~ 4095")
  let(b = matter_payload_bytes(passcode, discriminator, vid, pid, flow, rendezvous))
  str("MT:",
      join([for (g = [0 : 2]) b38_chunk(b[3 * g] + b[3 * g + 1] * 256 + b[3 * g + 2] * 65536, 5)]),
      b38_chunk(b[9] + b[10] * 256, 4));

// Verhoeff 校验
VD = [[0,1,2,3,4,5,6,7,8,9],[1,2,3,4,0,6,7,8,9,5],[2,3,4,0,1,7,8,9,5,6],
      [3,4,0,1,2,8,9,5,6,7],[4,0,1,2,3,9,5,6,7,8],[5,9,8,7,6,0,4,3,2,1],
      [6,5,9,8,7,1,0,4,3,2],[7,6,5,9,8,2,1,0,4,3],[8,7,6,5,9,3,2,1,0,4],
      [9,8,7,6,5,4,3,2,1,0]];
VP = [[0,1,2,3,4,5,6,7,8,9],[1,5,7,6,2,8,3,0,9,4],[5,8,0,3,7,9,6,1,4,2],
      [8,9,1,6,0,4,3,5,2,7],[9,4,5,3,1,2,6,8,7,0],[4,2,8,6,5,7,3,9,0,1],
      [2,7,9,3,8,0,6,4,1,5],[7,0,4,6,9,1,3,2,5,8]];
VINV = [0,4,3,2,1,5,6,7,8,9];

function verhoeff_c(ds, i = 0, c = 0) =
  i >= len(ds) ? c
  : verhoeff_c(ds, i + 1, VD[c][VP[(i + 1) % 8][ds[len(ds) - 1 - i]]]);
function verhoeff_check(s) =
  VINV[verhoeff_c([for (i = [0 : len(s) - 1]) search(s[i], "0123456789")[0]])];

// 11 位手动配对码（标准流程，不含 VID/PID），返回不带分隔符的字符串
function matter_manual_code(passcode, discriminator) =
  let(sd = floor(discriminator / 256),                 // 4 位 short discriminator
      digits = str(floor(sd / 4),
                   pad_zero((sd % 4) * 16384 + passcode % 16384, 5),
                   pad_zero(floor(passcode / 16384), 4)))
  str(digits, verhoeff_check(digits));

// 4-3-4 分组，便于阅读
function matter_manual_code_pretty(code) =
  str(substr(code, 0, 4), "-", substr(code, 4, 3), "-", substr(code, 7, 4));
function substr(s, start, n) = join([for (i = [start : start + n - 1]) s[i]]);

/* ---------- QR 编码：固定 Version 1 (21x21)、纠错等级 L、字母数字模式 ---------- */
// Matter payload 共 22 个字符（MT: + 19 位 Base38），Version 1-L 最多容纳 25 个。

QN = 21;
AN_TABLE = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ $%*+-./:";

// GF(256) 表（本原多项式 0x11D）
function gf_exp_build(i, e, acc) =
  i >= 255 ? acc
  : gf_exp_build(i + 1, e * 2 >= 256 ? xor_n(e * 2, 285, 9) : e * 2, concat(acc, [e]));
GF_EXP = gf_exp_build(0, 1, []);
GF_LOG = [for (v = [0 : 255]) v == 0 ? 0 : search(v, GF_EXP)[0]];
function gf_mul(a, b) = (a == 0 || b == 0) ? 0 : GF_EXP[(GF_LOG[a] + GF_LOG[b]) % 255];

// 7 个纠错码字的生成多项式系数
RS_GEN = [127, 122, 154, 164, 11, 68, 117];
function rs_step(rem, d) =
  let(f = xor_n(d, rem[0], 8))
  [for (i = [0 : 6]) xor_n(i < 6 ? rem[i + 1] : 0, gf_mul(RS_GEN[i], f), 8)];
function rs_rem(data, i = 0, rem = [0, 0, 0, 0, 0, 0, 0]) =
  i >= len(data) ? rem : rs_rem(data, i + 1, rs_step(rem, data[i]));

function an_val(c) = search(c, AN_TABLE)[0];
function an_bits(s) = concat_all([
  for (i = [0 : 2 : len(s) - 1])
    i + 1 < len(s) ? bits_msb(45 * an_val(s[i]) + an_val(s[i + 1]), 11)
                   : bits_msb(an_val(s[i]), 6)
]);

// 19 个数据码字
function qr_data_cw(s) =
  let(b0 = concat_all([[0, 0, 1, 0], bits_msb(len(s), 9), an_bits(s)]))
  assert(len(b0) <= 152, "文本太长，Version 1-L 放不下")
  let(b1 = concat(b0, zeros(min(4, 152 - len(b0)))),
      b2 = concat(b1, zeros((8 - len(b1) % 8) % 8)),
      bytes = [for (k = [0 : len(b2) / 8 - 1])
                 [for (i = [0 : 7]) b2[8 * k + i]] * [128, 64, 32, 16, 8, 4, 2, 1]])
  concat(bytes, [for (i = [0 : 18]) if (i >= len(bytes)) (i - len(bytes)) % 2 == 0 ? 236 : 17]);

function qr_codeword_bits(s) =
  let(d = qr_data_cw(s))
  concat_all([for (c = concat(d, rs_rem(d))) bits_msb(c, 8)]);

// 功能图形区域（定位图形+分隔符+格式信息、定时线）
function is_func(r, c) =
  (r < 9 && c < 9) || (r < 9 && c >= 13) || (r >= 13 && c < 9) || r == 6 || c == 6;

// 数据模块的蛇形填充顺序，值为 r*QN + c
DATA_ORDER = [
  for (j = [0 : 9])
    let(cr = j < 7 ? 20 - 2 * j : 19 - 2 * j)
    for (t = [0 : 20]) for (dc = [0 : 1])
      let(r = j % 2 == 0 ? 20 - t : t, c = cr - dc)
      if (!is_func(r, c)) r * QN + c
];

// 纠错等级 L 的 15 位格式信息，下标为掩码编号
FMT_L = [30660, 29427, 32170, 30877, 26159, 25368, 27713, 26998];

function fmt_idx(r, c) =
  c == 8 && r <= 5 ? r
  : c == 8 && r == 7 ? 6
  : r == 8 && c == 8 ? 7
  : r == 8 && c == 7 ? 8
  : r == 8 && c <= 5 ? 14 - c
  : r == 8 && c >= 13 ? 20 - c
  : c == 8 && r >= 14 ? r - 6
  : -1;

function finder_dark(r, c) =
  r == 0 || r == 6 || c == 0 || c == 6 || (r >= 2 && r <= 4 && c >= 2 && c <= 4);

function mask_flip(m, r, c) =
  (m == 0 ? (r + c) % 2
 : m == 1 ? r % 2
 : m == 2 ? c % 3
 : m == 3 ? (r + c) % 3
 : m == 4 ? (floor(r / 2) + floor(c / 3)) % 2
 : m == 5 ? (r * c) % 2 + (r * c) % 3
 : m == 6 ? ((r * c) % 2 + (r * c) % 3) % 2
 :          ((r + c) % 2 + (r * c) % 3) % 2) == 0;

function qr_dark(bits, mask, r, c) =
  !is_func(r, c) ? (bits[search(r * QN + c, DATA_ORDER)[0]] == 1) != mask_flip(mask, r, c)
  : (r < 7 && c < 7) ? finder_dark(r, c)
  : (r < 7 && c >= 14) ? finder_dark(r, c - 14)
  : (r >= 14 && c < 7) ? finder_dark(r - 14, c)
  : fmt_idx(r, c) >= 0 ? floor(FMT_L[mask] / pow(2, fmt_idx(r, c))) % 2 == 1
  : (r == 13 && c == 8) ? true
  : (r == 6 && c >= 8 && c <= 12) ? c % 2 == 0
  : (c == 6 && r >= 8 && r <= 12) ? r % 2 == 0
  : false;

// 返回 21x21 的 0/1 矩阵，matrix[行][列]，行 0 在最上
// mask 为掩码编号（0~7，任意一个都是合法的 QR 码）
function qr_matrix(text, mask = 0) =
  let(bits = qr_codeword_bits(text))
  [for (r = [0 : QN - 1]) [for (c = [0 : QN - 1]) qr_dark(bits, mask, r, c) ? 1 : 0]];

/* ---------- 几何 ---------- */

// 2D：深色模块，左下角为原点，m 为模块边长
module qr_2d(matrix, m = 1) {
  n = len(matrix);
  for (r = [0 : n - 1]) for (c = [0 : n - 1])
    if (matrix[r][c] == 1)
      translate([c * m, (n - 1 - r) * m]) square(m + 0.01);
}

// 七段数码管风格的数字，自己用矩形拼，不依赖系统字体。
// 左下角为原点，w×h 为字符框，t 为笔画宽度。段顺序 a b c d e f g：
//   a 上横  b 右上竖  c 右下竖  d 下横  e 左下竖  f 左上竖  g 中横
SEG7 = [[1,1,1,1,1,1,0], [0,1,1,0,0,0,0], [1,1,0,1,1,0,1], [1,1,1,1,0,0,1],
        [0,1,1,0,0,1,1], [1,0,1,1,0,1,1], [1,0,1,1,1,1,1], [1,1,1,0,0,0,0],
        [1,1,1,1,1,1,1], [1,1,1,1,0,1,1]];

module seg7_digit(d, w, h, t) {
  s = SEG7[d];
  half = (h + t) / 2;   // 单侧竖笔的高度，上下两段在中横处重叠
  if (s[0]) translate([0, h - t]) square([w, t]);
  if (s[1]) translate([w - t, (h - t) / 2]) square([t, half]);
  if (s[2]) translate([w - t, 0]) square([t, half]);
  if (s[3]) square([w, t]);
  if (s[4]) square([t, half]);
  if (s[5]) translate([0, (h - t) / 2]) square([t, half]);
  if (s[6]) translate([0, (h - t) / 2]) square([w, t]);
}

// 每个字符的前进距离：数字 w + sp，分隔符 "-" 用 dl + sp
function seg7_adv(c, w, dl, sp) = c == "-" ? dl + sp : w + sp;
function seg7_x(code, i, w, dl, sp) =
  i == 0 ? 0 : sum([for (j = [0 : i - 1]) seg7_adv(code[j], w, dl, sp)]);
function seg7_width(code, w, dl, sp) = seg7_x(code, len(code), w, dl, sp) - sp;

// 以原点为水平中心、垂直中心绘制一串数字（含 "-"）
module seg7_text(code, h, t, sp = 0.45) {
  w = 0.55 * h;       // 字符宽度
  dl = 0.4 * w;       // 分隔符长度
  total = seg7_width(code, w, dl, sp);
  translate([-total / 2, -h / 2])
    for (i = [0 : len(code) - 1]) {
      c = code[i];
      translate([seg7_x(code, i, w, dl, sp), 0])
        if (c == "-") translate([0, (h - t) / 2]) square([dl, t]);
        else seg7_digit(search(c, "0123456789")[0], w, h, t);
    }
}

// 2D：QR 居中于原点上方，配对码在其下方。可直接用于凹刻
// text_size 为数字高度，seg_t 为笔画宽度（建议 ≥ 0.7，即 2 条挤出线）
module matter_label_2d(passcode, discriminator, vid = 0, pid = 0, flow = 0,
                       rendezvous = 2, m = 1.2, text_size = 4, gap = 3, mask = 0,
                       seg_t = 0.7) {
  qr = qr_matrix(matter_qr_payload(passcode, discriminator, vid, pid, flow, rendezvous), mask);
  code = matter_manual_code_pretty(matter_manual_code(passcode, discriminator));
  w = len(qr) * m;

  translate([-w / 2, 0]) qr_2d(qr, m);
  translate([0, -gap - text_size / 2])
    seg7_text(code, text_size, seg_t);
}

// 3D：一块平板，图案凹刻在 z=0 的底面，从底部看是正读的。
// 单色打印机用换色实现黑白：按颜色拆成互不重叠的几块
//   z = 0 ~ depth                  白色整层，深色模块位置镂空（凹槽）
//   z = depth ~ depth + dark_t     黑色"岛"（只覆盖二维码区域）
//                                  + 白色外圈（外壳侧壁），两者之间留 iso_gap 的空隙
//   z = depth + dark_t ~ thickness 白色整层，盖住空隙
// 黑色岛与白色外圈在该层里是互相分离的两块，切片后黑色部分在 G-code 里是
// 一段连续的打印，只需在它前后各插入一次 M600（层高 0.2mm 时就是第 3 层）：
//   白色外圈 → M600 换黑 → 黑色岛 → M600 换白 → 下一层。
// 空隙在内部被上下白层封住，外面看不到；侧壁全程是白色，没有黑线。
// float_gap：黑色岛下表面与下方白色面之间的悬空间隙（岛区域内白色顶面下降 float_gap，
// 凹槽顶面仍是黑色岛）。注意 float_gap 小于层高时，切片软件会把它四舍五入掉。
// 打印面积会比二维码区域大 2 * (iso_gap + wall)，二维码和文字大小不变。
module matter_label_plate(passcode, discriminator, vid = 0, pid = 0, flow = 0,
                          rendezvous = 2, m = 1.5, text_size = 4, gap = 1,
                          mask = 0, thickness = 2, depth = 0.6, dark_t = 0.2,
                          margin = 3, iso_gap = 1.2, wall = 1.6, float_gap = 0.2) {
  assert(float_gap >= 0 && float_gap < depth, "float_gap 必须在 0 ~ depth 之间");
  assert(thickness > depth + dark_t, "thickness 必须大于 depth + dark_t");
  qr_w = QN * m;
  plate_w = qr_w + 2 * margin;
  plate_h = qr_w + gap + text_size + 2 * margin;
  ext = iso_gap + wall;   // 外壳比黑色岛每边多出的宽度
  // 内容 y 范围：[-gap-text_size, qr_w]；e 为相对黑色岛每边外扩的距离
  module slab(z0, z1, e = 0)
    translate([-plate_w / 2 - e, -gap - text_size - margin - e, z0])
      cube([plate_w + 2 * e, plate_h + 2 * e, z1 - z0]);

  echo(str("黑色层 z=", depth, "~", depth + dark_t, "mm：白色外圈打完后 M600 换黑打黑色岛，",
           "岛打完后 M600 换回白"));
  echo(str("外壳尺寸 ", plate_w + 2 * ext, " x ", plate_h + 2 * ext, " mm"));

  color("white") difference() {
    slab(0, depth, ext);
    // 黑色岛正下方的白色顶面下降 float_gap，形成悬空间隙
    // 范围比黑色岛每边多出 iso_gap / 2，避免凹陷边缘与岛的底边重合（非流形）
    slab(depth - float_gap, depth + 0.01, iso_gap / 2);
    translate([0, 0, -0.01])
      linear_extrude(depth + 0.02)
        mirror([1, 0, 0])   // 底面朝外，镜像后从下往上看才是正的
          matter_label_2d(passcode, discriminator, vid, pid, flow, rendezvous,
                          m, text_size, gap, mask);
  }
  color("black") slab(depth, depth + dark_t);
  color("white") difference() {
    slab(depth, depth + dark_t, ext);
    slab(depth - 0.01, depth + dark_t + 0.01, iso_gap);
  }
  color("white") slab(depth + dark_t, thickness, ext);
}

/* ---------- 外壳：2D 轮廓 ---------- */

module cav2d() {
  offset(r = rc) offset(delta = -rc)
    translate([cav_x0, cav_y0]) square([cav_x1 - cav_x0, cav_y1 - cav_y0]);
}
module out2d() { offset(r = W) cav2d(); }
module mid2d() { offset(r = W / 2) cav2d(); }
// 底壳墙顶内侧的台阶，上盖的舌头插进去
module notch_ring2d() { difference() { mid2d(); cav2d(); } }
module tongue2d() { difference() { offset(r = W / 2 - tc) cav2d(); cav2d(); } }

// 螺丝立柱的 2D：圆柱加一条延伸进墙里的带子，和墙连成一体
module boss2d(s, r) {
  hull() {
    translate([s[0], s[1]]) circle(r);
    translate([s[0] - r, (s[2] < 0 ? cav_y0 - 0.5 : cav_y1 + 0.5) - 0.005]) square([2 * r, 0.01]);
  }
}

/* ---------- 底壳 ---------- */

module base_walls() {
  linear_extrude(z_split) difference() { out2d(); cav2d(); }
}

module base_floor() {
  // 二维码板占据的矩形先挖掉，由 matter_label_plate 填回去
  difference() {
    linear_extrude(F) out2d();
    translate([lbl_x - lbl_w / 2 + eps, lbl_y - (lbl_gap + lbl_text + lbl_margin + lbl_ext) + eps, -eps])
      cube([lbl_w - 2 * eps, lbl_h - 2 * eps, F + 2 * eps]);
  }
  translate([lbl_x, lbl_y, 0])
    matter_label_plate(qr_passcode, qr_discriminator, qr_vid, qr_pid,
                       m = lbl_m, text_size = lbl_text, gap = lbl_gap, margin = lbl_margin,
                       thickness = F, iso_gap = lbl_iso, wall = lbl_wall);
}

module base() {
  difference() {
    union() {
      base_floor();
      base_walls();
      // 螺丝立柱
      for (s = screws) linear_extrude(z_split) boss2d(s, boss_r);
      // PCB 支撑垫
      for (p = pads)
        translate([p[0], p[1], F - eps]) cube([p[2] - p[0], p[3] - p[1], under + eps]);
    }
    // 墙顶内侧台阶
    translate([0, 0, z_split - lip_h]) linear_extrude(lip_h + 1) notch_ring2d();
    // 螺丝过孔和沉头
    for (s = screws) {
      translate([s[0], s[1], -1]) cylinder(d = screw_d, h = z_split + 2);
      translate([s[0], s[1], -eps]) cylinder(d1 = csk_d, d2 = screw_d, h = csk_h + eps);
    }
    usb_cuts();
  }
}

// USB-C 开口，底壳和上盖共用
module usb_cuts() {
  zc = zb + (h_usb - usb_below) / 2;
  for (yc = usb_y)
    translate([cav_x1 - eps, yc - usb_open_w / 2, zc - usb_open_h / 2])
      cube([W + 1, usb_open_w, usb_open_h]);
}

/* ---------- 上盖 ---------- */

// 导杆的杆尖和各部分的高度
z_tip = zb + h_key + key_gap;                      // 杆尖
z_col = z_tip + tip_len;                           // 圆环底面
z_tube_end = z_col + col_t + fuse_gap;             // 导向管端面
pad_cb = pad_t + btn_stroke;                       // 沉孔深度

module lid_shell() {
  difference() {
    union() {
      translate([0, 0, z_split]) linear_extrude(z_hi_top - z_split) out2d();
      // 插进底壳台阶的舌头
      translate([0, 0, z_split - lip_h]) linear_extrude(lip_h + eps) tongue2d();
    }
    translate([0, 0, z_split - eps]) linear_extrude(z_hi_in - z_split + eps) cav2d();
  }
}

// 导杆（以按键中心 c 为轴）。圆环靠断点柱和导向管端面相连
module plunger(c) {
  translate([c[0], c[1], 0]) {
    translate([0, 0, z_hi_top - pad_t]) cylinder(r = pad_r, h = pad_t);
    translate([0, 0, z_tip]) cylinder(d = pin_d, h = z_hi_top - pad_t - z_tip + eps);
    translate([0, 0, z_col]) cylinder(d = col_d, h = col_t);
    for (a = [0 : fuse_n - 1]) rotate(a * 360 / fuse_n)
      translate([(pin_d / 2 + pin_clr + col_d / 2) / 2, 0, z_col + col_t - eps])
        cylinder(d = fuse_d, h = fuse_gap + 2 * eps);
  }
}

// LED 模块的两个安装孔位置（板坐标）
function mod_holes() = [for (sx = [-1, 1]) [mod_cx + sx * mod_hole_dx, mod_cy + mod_hole_dy]];
z_pocket_top = z_hi_in + mod_pocket;                // 凹槽顶面，模块贴在这里
z_post_tip = z_pocket_top - mod_t + post_tip;       // 立柱尖

module lid() {
  difference() {
  union() {
    difference() {
      union() {
        lid_shell();
        // 螺丝立柱，下端落在分型面上，与底壳立柱对接
        for (s = screws) translate([0, 0, z_split]) linear_extrude(z_hi_in - z_split + eps) boss2d(s, boss_r);
        // 导向管
        for (c = key_xy) translate([c[0], c[1], z_tube_end]) cylinder(d = tube_od, h = z_hi_in - z_tube_end + eps);
      }
      for (s = screws) translate([s[0], s[1], z_split - eps]) cylinder(d = pilot_d, h = pilot_depth);
      usb_cuts();
      for (i = [0 : len(key_xy) - 1]) {
        c = key_xy[i];
        // 导杆孔（贯穿导向管和顶板）和顶面的圆帽沉孔
        translate([c[0], c[1], z_tube_end - 1]) cylinder(r = pin_d / 2 + pin_clr, h = z_hi_top - pad_cb - z_tube_end + 1 + eps);
        translate([c[0], c[1], z_hi_top - pad_cb]) cylinder(r = pad_r + pad_clr, h = pad_cb + 1);
        // 按键名称，刻在顶面
        translate([c[0], -3.0, z_hi_top - label_text_depth])
          linear_extrude(label_text_depth + eps)
            text(key_names[i], size = label_text_size, halign = "center", valign = "center");
      }
      // LED：模块凹槽 + 完全镂空的窗口
      translate([mod_cx - (mod_w + 2 * mod_clr) / 2, mod_cy - (mod_d + 2 * mod_clr) / 2, z_hi_in - eps])
        cube([mod_w + 2 * mod_clr, mod_d + 2 * mod_clr, mod_pocket + eps]);
      translate([mod_cx + led_dx - led_win / 2, mod_cy + led_dy - led_win / 2, z_hi_in])
        cube([led_win, led_win, TR + 1]);
    }
    for (c = key_xy) plunger(c);
    // 穿过模块安装孔的立柱，从凹槽顶面往下长（凹槽挖完之后再加）
    for (h = mod_holes()) translate([h[0], h[1], z_post_tip]) cylinder(d = post_d, h = z_pocket_top - z_post_tip + eps);
  }
  // M2 自攻孔：从立柱尖往上钻进顶板，不能穿透顶面
  for (h = mod_holes()) translate([h[0], h[1], z_post_tip - eps]) cylinder(d = m2_pilot_d, h = m2_pilot_depth + eps);
  }
}

/* ---------- 示意体：开发板和 LED 模块（只用来看装配和查干涉） ---------- */

module bx(x0, y0, x1, y1, z0, z1) { translate([x0, y0, z0]) cube([x1 - x0, y1 - y0, z1 - z0]); }

module ghost() {
  tail = pin_tail - pcb_t;   // 针伸出 PCB 底面的长度
  color("darkgreen") bx(0, 0, pcb_w, pcb_h, zb - pcb_t, zb);
  color("silver") {
    bx(9.8, 0.57, 25.6, 18.17, zb, zb + h_s3_module);                 // S3 模块
    bx(8.7, -ant_s3_down, 26.7, 19.22, zb, zb + 0.8);                 // S3 天线区
    bx(2.94, 39.76, 16.14, pcb_h + ant_h2_up, zb, zb + h_h2_module);  // H2 模块
    for (yc = usb_y) bx(43.53, yc - usb_w / 2, 51.41, yc + usb_w / 2, zb - usb_below, zb + h_usb);
  }
  color("black") {
    bx(1.53, 1.39, 6.56, 34.28, zb, zb + h_j4_body);                  // J4 塑料座
    for (k = [0 : 12]) for (x = [2.77, 5.31])
      bx(x - 0.32, 2.59 + 2.54 * k - 0.32, x + 0.32, 2.59 + 2.54 * k + 0.32, zb - tail, zb + 8.58);
    bx(j5_box[0], j5_box[1], j5_box[2], j5_box[3], zb - tail, zb + h_j5_pin);   // J5
    for (c = key_xy) bx(c[0] - 2.1, c[1] - 1.6, c[0] + 2.1, c[1] + 1.6, zb, zb + h_key);
  }
  // 2x3 排母，及内列三个尾针
  color("dimgray") {
    bx(socket_box[0], socket_box[1], socket_box[2], socket_box[3], zb + h_j4_body, zb + h_j4_body + socket_h);
    for (yc = [33.07, 30.53, 27.99])
      bx(5.31 - 0.4, yc - 0.4, 5.31 + 0.4, yc + 0.4, zb + h_j4_body + socket_h, zb + h_j4_body + socket_h + socket_tail);
  }
  // WS2812 模块：贴在凹槽顶面，LED 朝上伸进窗口；带两个安装孔，3 个输入排针在左边
  color("orange") {
    z1 = z_hi_in + mod_pocket;
    difference() {
      bx(mod_cx - mod_w / 2, mod_cy - mod_d / 2, mod_cx + mod_w / 2, mod_cy + mod_d / 2, z1 - mod_t, z1);
      for (h = mod_holes()) translate([h[0], h[1], z1 - mod_t - 1]) cylinder(d = mod_hole_d, h = mod_t + 2);
    }
    bx(mod_cx + led_dx - led_pkg / 2, mod_cy + led_dy - led_pkg / 2,
       mod_cx + led_dx + led_pkg / 2, mod_cy + led_dy + led_pkg / 2, z1, z1 + led_h);
    bx(mod_cx + mod_pin_x - 1, mod_cy + mod_pin_y0 - 1, mod_cx + mod_pin_x + 1, mod_cy + mod_pin_y0 + 5.08 + 1,
       z1 - mod_t - mod_pins, z1 - mod_t);
  }
  // M2 垫圈和螺丝头（背面）
  color("steelblue") for (h = mod_holes()) translate([h[0], h[1], z_hi_in + mod_pocket - mod_t - 0.3]) {
    cylinder(d = 5, h = 0.3);
    translate([0, 0, -1.3]) cylinder(d = 3.8, h = 1.3);
  }
}

/* ---------- 输出 ---------- */

if (part == "base") base();
else if (part == "lid") translate([0, 0, z_hi_top]) rotate([180, 0, 0]) lid();
else if (part == "assembly") { color("white") base(); color("lightgray") lid(); ghost(); }
else if (part == "exploded") { color("white") base(); translate([0, 0, 25]) color("lightgray") lid(); ghost(); }
else if (part == "check_base") intersection() { base(); ghost(); }
else if (part == "check_lid") intersection() { lid(); ghost(); }
else if (part == "check_fit") intersection() { base(); lid(); }

echo(str("外壳外形 ", cav_x1 - cav_x0 + 2 * W, " x ", cav_y1 - cav_y0 + 2 * W, " mm"));
echo(str("PCB 顶面 z=", zb, "，分型面 z=", z_split, "，上盖顶面 z=", z_hi_top));
echo(str("二维码板 ", lbl_w, " x ", lbl_h, " mm，中心 x=", lbl_x));
echo(payload = matter_qr_payload(qr_passcode, qr_discriminator, qr_vid, qr_pid));
echo(manual_code = matter_manual_code(qr_passcode, qr_discriminator));
