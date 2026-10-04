# 由操作者指定平台路线

## 问题与修复

`pvpanic-route-sol-medium-20261003-08` 的 experiment.json 写了 KVM，实际启动却没有
把这个选择传入项目配置。模型收到 `<SELECTED_kvm_OR_tcg>`，依据 WSL2 错选 TCG。
基线构建通过，启动在解包 initramfs 时超过 120 秒；改选 KVM 又被禁止路线替换的规则拒绝。
这是配置传递缺陷，不能归因为驱动、缺少工具链或公开测试断言。

现在只有一条配置来源：

1. 操作者通过 `--platform-image IMAGE --platform-accelerator kvm` 创建项目。
2. 配置进入现有 ProjectConfig 和项目持久化记录，不新建另一份选路文件。
3. 启动前检查参数齐备；续跑读取已存配置，显式传入不同值会在模型调用前报错。
4. 工作包直接展示 Docker 镜像、加速器、固定 OVMF 路径和设备传入方式。
   已配置的托管路线不再夹带宿主盘点、候选路线和 prepare-smoke 占位模板，避免诱发重新选路。
5. 模型只调用 `driver_checks.platform {"action":"bootstrap"}`；CLI 对应
   `environment bootstrap RUN`。底层 `platform prepare` 同样从项目配置取值。
6. 原有平台 profile、镜像身份检查、构建/启动收据和最终验收保持原用途。
   选 KVM 而 `/dev/kvm` 缺失时报告该错误，不降级 TCG、不拉取其他镜像。

启动参数示例（添加到正常 run-experiment.sh 命令）：

```sh
--platform-image asterinas/dev:0.18.1-20260805-dpf-ovmf-bar-v1 \
--platform-accelerator kvm
```

OVMF 固定为镜像内 `/root/ovmf/release/OVMF.fd`，使用现有适配器统一定义。
不会根据本机 Docker 镜像列表自动选择镜像，也不根据宿主系统猜加速器。
TCG 仍可由操作者显式选择；本次修复没有宣称 TCG 已验证或调整其超时。

## Docker 与宿主

Docker 是本项目构建和 QEMU 进程的执行环境。Docker 底层宿主可以是 Linux/WSL2；
这和是否使用容器并不冲突。WSL2 字样不能证明 KVM 不可用，也不能推出跨架构模拟。
KVM 路线由控制器把宿主 `/dev/kvm` 传入 QEMU 容器，实际启动收据才证明它能够工作。

## 本次状态

代码和当前入口文档已修改。按用户要求，本轮没有运行测试、构建、QEMU 或付费模型实验。
这不是新的运行成功证据。实验 08 的冻结 controller、配置、失败记录均未修改，也未重启。
旧实验没有这两个项目配置字段，应继续用其冻结控制器审计；不能手改数据库或
向旧项目补字段冒充原本已正确配置。下一次新实验必须通过上述实际启动参数配置路线，
不能只在实验说明文件写 KVM。
