# 发布到 GitHub

本仓库已经整理为“公开内容 + submodule + 私有资产”的结构。按以下步骤发布。

## 0. 先确认

```bash
cd <仓库根目录>
git status                     # 当前所有文件已 staged，尚未 commit
git ls-files | wc -l           # 约 232 个文件（不含 submodule 内容）
du -sh .git                    # 约 9 MB（含本地 submodule 对象）
./scripts/check-no-private.sh  # 必须通过
```

> 当前工作区里 `git status` 可能显示 `Am vendor/aflnet`：这是本地为构建
> 应用 AFLNet 补丁后的工作树修改；父仓库只会提交 gitlink（官方 commit
> `96032f8`），不会提交这些文件。不要对 submodule 做 `git add -f`。
>
> 本目录目前**没有 commit**（环境里没有配置 git 身份）。先设置：

```bash
git config user.name  "<你的名字>"
git config user.email "<你的邮箱>"
git commit -m "Initial public release: C2900 IOS fuzzing prototype"
```

## 1. 创建空仓库并推送

在 GitHub 建一个空仓库（不要勾选 README/.gitignore），然后：

```bash
git branch -M main
git remote add origin git@github.com:<account>/C2900SysFuzzer.git
git push -u origin main
```

> 不要加 `--recurse-submodules` 推送：submodule 指向官方 `aflnet/aflnet`，
> 该 commit 已在远端；本地补丁由父仓库的 `patches/aflnet.patch` 提供。

## 2. 确认 submodule 提交可达

`.gitmodules` 指向官方 `https://github.com/aflnet/aflnet.git`，
gitlink 钉在 `96032f86d0005dfeeb41ea7b31103f1d1ff8f168`（官方仓库已存在）。
`build.sh` 会应用 `patches/aflnet.patch` 获得 `-Z` 系统模式支持。

## 3. 发布后自检

```bash
# 全新目录
git clone --recursive git@github.com:<account>/C2900SysFuzzer.git /tmp/c2900-check
cd /tmp/c2900-check
git submodule status                       # 应显示 96032f8
! git ls-files | grep -E 'vendor/(qemu/src|qemu/bin|qemu/lib|c2900/firmware|c2900/lib)/'
! git ls-files | grep -E 'private-assets'
./scripts/check-no-private.sh
```

然后放入合并资产包并一键构建：

```bash
cp -a /path/to/c2900-private-assets vendor/
./build.sh
```

## 4. 不要做的事

- 不要 `git add -f` 固件、`vendor/qemu/src`、`libmachcore.so`、运行库
  或 `vendor/*private-assets*/` 目录；它们在 `.gitignore` 中，
  且 GitHub 单文件限制 100 MB。
- 不要把私有资产放进 GitHub Release 或 LFS。
- 不要在提交历史里留下固件后再删除：需要 rewrite history 才能清除。
- 仓库描述与 README 已写明“不含固件/厂商源码，需私下获取”。
