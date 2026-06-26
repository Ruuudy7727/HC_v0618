# Git 命令速查（本项目实际场景）

> 整理自 HC_v0618 项目在本地 Windows 与 Linux 服务器上的真实操作场景，便于日后查阅。

---

## 一、日常查看状态

### 1. `git status`

**场景：** 看当前改了什么、有没有未提交内容、和远程是否同步。

```bash
git status
```

**常见输出含义：**

| 输出 | 含义 |
|------|------|
| `Changes not staged for commit` | 有修改，还没 `git add` |
| `Untracked files` | 新文件，Git 还没跟踪（如 `rag_data/`、`env.ipynb`） |
| `Your branch is ahead of 'origin/main' by N commit` | 本地有 N 个提交还没 push |
| `Your branch is up to date with 'origin/main'` | 和远程一致 |
| `nothing to commit, working tree clean` | 工作区干净 |

---

### 2. `git log --oneline -N`

**场景：** 看最近 N 条提交，确认是否 pull / push 成功。

```bash
git log --oneline -3
```

| 参数 | 含义 |
|------|------|
| `--oneline` | 每条一行，只显示 hash + 说明 |
| `-3` | 只看最近 3 条（数字可改） |

**示例输出：**

```
d80a8f7 改为流式输出
aa487b5 入库第一个手册
e78b1a9 第二次提交
```

---

### 3. `git diff`

**场景：** 看具体改了哪些行（例如服务器上 `app.py` 改了什么）。

```bash
# 未暂存的改动
git diff

# 指定文件
git diff app.py

# 已暂存、待 commit 的改动
git diff --staged
```

---

### 4. 对比本地和远程差多少提交

**场景：** push 被拒绝，想知道本地和远程谁多谁少。

```bash
git fetch origin

# 远程有、本地没有的提交
git log --oneline main..origin/main

# 本地有、远程没有的提交
git log --oneline origin/main..main
```

---

## 二、提交并推送到 GitHub

### 5. `git add` + `git commit`

**场景：** 改完代码，提交到本地仓库。

```bash
# 添加所有改动（含新文件）
git add .

# 只添加指定文件
git add app.py api_server.py

# 提交
git commit -m "改为流式输出"
```

| 命令 | 含义 |
|------|------|
| `git add .` | 当前目录下所有变更加入暂存区 |
| `git add <file>` | 只加指定文件 |
| `git commit -m "说明"` | 用 `-m` 直接写提交说明 |

---

### 6. `git push`

**场景：** 把本地提交推到 GitHub。

```bash
git push
# 或首次 / 明确指定：
git push origin main
```

| 部分 | 含义 |
|------|------|
| `origin` | 远程仓库别名（默认） |
| `main` | 分支名 |

**常见失败：**

```
! [rejected] main -> main (fetch first)
```

表示远程有你本地没有的提交，需要先 pull 再 push。

---

## 三、拉取远程代码

### 7. `git pull`（需已设置 upstream）

**场景：** 本地已关联 `origin/main` 时，直接拉最新代码。

```bash
git pull
```

等价于：`git fetch` + `git merge`。

---

### 8. `git pull origin main`（未设置 upstream 时用）

**场景：** 服务器 clone 后没设跟踪分支，直接 `git pull` 会报错：

```
There is no tracking information for the current branch.
```

**解决：**

```bash
git pull origin main
```

| 参数 | 含义 |
|------|------|
| `origin` | 从哪个远程拉 |
| `main` | 拉哪个分支 |

**拉成功后建议设 upstream，以后可直接 `git pull`：**

```bash
git branch --set-upstream-to=origin/main main
```

| 参数 | 含义 |
|------|------|
| `--set-upstream-to=origin/main` | 本地 `main` 跟踪远程 `origin/main` |
| 最后的 `main` | 当前要设置的分支 |

---

### 9. `git pull --rebase origin main`（推荐：历史更干净）

**场景：** 本地和远程都有新提交（分叉），例如：

- 远程：`入库第一个手册`
- 本地：`改为流式输出`

用 rebase 把你的提交「接在」远程最新提交后面，避免多余 merge 节点。

```bash
git fetch origin
git pull --rebase origin main
git push origin main
```

| 参数 | 含义 |
|------|------|
| `--rebase` | 不生成 merge commit，把本地提交挪到远程之后 |

**对比：**

| 方式 | 结果 |
|------|------|
| `git pull`（merge） | 多一个「Merge branch...」提交 |
| `git pull --rebase` | 历史一条线，更整洁 |

---

## 四、处理本地改动冲突

### 10. `git restore <file>`（丢弃本地修改）

**场景：** 服务器上 `app.py` 有改动，但要用远程版本。

```bash
git restore app.py
```

| 说明 | |
|------|--|
| 作用 | 把文件恢复成最后一次 commit 的状态 |
| 注意 | **不可恢复**，改之前先 `git diff app.py` 确认 |

旧写法（仍可用）：`git checkout -- app.py`

---

### 11. `git stash`（临时存改动）

**场景：** 有本地修改想保留，但又要先 pull。

```bash
# 暂存
git stash push -m "server app.py" -- app.py

git pull origin main

# 恢复
git stash pop
```

| 命令 | 含义 |
|------|------|
| `git stash push -m "说明" -- app.py` | 只 stash 指定文件 |
| `git stash pop` | 恢复并删除 stash |
| `git stash list` | 查看 stash 列表 |

---

### 12. 未跟踪文件挡住 pull（`rag_data/` 问题）

**场景：** pull 报错：

```
error: The following untracked working tree files would be overwritten by merge:
    rag_data/all/chroma.sqlite3
    ...
Please move or remove them before you merge. Aborting.
```

**原因：** 远程仓库里已有这些文件，本地同名路径是「未跟踪」的，Git 不敢覆盖。

**解决（先备份再 pull）：**

```bash
mv rag_data rag_data.bak
git pull origin main
```

确认无误后可删备份：

```bash
rm -rf rag_data.bak
```

可选：对比备份与 Git 版本是否一致：

```bash
diff -rq rag_data/all rag_data.bak/all | head
```

---

## 五、完整工作流速查

### 场景 A：本地改完代码，推 GitHub

```bash
git status
git add .
git commit -m "说明这次改了什么"
git pull --rebase origin main   # 先拉，避免 push 被拒
git push origin main
git log --oneline -3            # 确认成功
```

---

### 场景 B：服务器更新到最新代码

```bash
cd ~/work/HC/HC_v0618
git status

# 若有 rag_data 冲突
mv rag_data rag_data.bak

# 若有 app.py 等不需要的本地修改
git restore app.py

git pull origin main
git branch --set-upstream-to=origin/main main
git log --oneline -3

# 重启服务（示例）
# uvicorn api_server:app --host 0.0.0.0 --port 50200
```

---

### 场景 C：push 被拒（fetch first）

```bash
git fetch origin
git log --oneline main..origin/main    # 看远程多了什么
git log --oneline origin/main..main    # 看本地多了什么
git pull --rebase origin main
git push origin main
```

---

## 六、本项目特别注意

| 文件 / 目录 | Git 行为 | 建议 |
|-------------|----------|------|
| `.env` | 在 `.gitignore`，**不会** push / pull | 服务器单独维护 |
| `rag_data/` | 已提交进仓库（入库数据） | pull 前若本地有未跟踪副本，先 `mv` 备份 |
| `env.ipynb` | 未跟踪 | 不影响 pull，不用 add |
| `models/` | 在 `.gitignore` | 服务器本地下载，不进 Git |

---

## 七、命令参数对照表

| 命令 | 常用参数 | 作用 |
|------|----------|------|
| `git status` | 无 | 看工作区状态 |
| `git log` | `--oneline -N` | 简洁查看最近提交 |
| `git diff` | `文件名` | 看具体改动 |
| `git add` | `.` 或 `文件` | 加入暂存区 |
| `git commit` | `-m "说明"` | 提交 |
| `git push` | `origin main` | 推送到远程 |
| `git pull` | `origin main` | 拉取并合并 |
| `git pull` | `--rebase origin main` | 拉取并 rebase |
| `git fetch` | `origin` | 只下载，不合并 |
| `git restore` | `文件名` | 丢弃未提交修改 |
| `git stash` | `push` / `pop` | 临时保存 / 恢复改动 |
| `git branch` | `--set-upstream-to=origin/main main` | 设置跟踪分支 |

---

*文档路径：`docs/git-速查.md`*
