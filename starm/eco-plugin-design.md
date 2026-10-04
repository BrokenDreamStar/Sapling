# ECO Leaves 插件化移植设计文档（v2·实测结论版）

- 更新：2026-10-04
- 状态：**M1 可行性全绿；M2 构建完成并在真实服务器启动通过；M3 压测进行中**
- 产物：`eco-leaves-port/dist/EntityCollisionOptimizer-Leaves.jar`（已安装到服务器 `plugins/`）
- 工作树：`/Users/starm/Development/Minecraft/eco-leaves-port`（branch `leaves-port`，基于 ECO `origin/26.1`）

---

## 1. 结论摘要

ECO 以 **leavesclip 插件形态**运行在 Leaves 26.1.2（Mopping/macOS arm64 实测）：
- 20 个 Mixin + MixinExtras 注入层 **原样保留**，无需转源码 patch
- 依赖 leavesclip 内置的 Fabric-Loader 衍生 Mixin 子系统（启动参数 `-Dleavesclip.enable.mixin=true`）
- native 后端已为 **darwin-arm64** 本地编译（服务器就是 arm64 macOS）
- `/eco` 命令、FFM 后端初始化、优雅停服全部验证通过

## 2. leavesclip 机制（源码级 + 实测）

1. 扫描 `plugins/*.jar` 中的 `leaves-plugin.json`（= JSON 版 Paper 插件描述符 + 扩展字段）
2. 把 jar 内 **`mixin.package-name` 前缀** 的全部条目 + 声明的 mixin 配置复制到 `plugins/.mixins/<name>.mixins.jar`（md5 缓存）
3. 挂到服务端类加载器，`Mixins.addConfiguration()` 注册配置
4. 内置 MixinExtras（`mixin-extras.init.mixins.json`，全局生效）

### 三条实测硬约束

| 约束 | 说明 |
| --- | --- |
| `"target": "@env(PREINIT)"` 必填 | leavesclip 环境停留在 PREINIT 阶段，默认目标（DEFAULT）的配置永不激活 |
| `compatibilityLevel` ≤ `JAVA_21` | leavesclip 的 ASM 9.5 拒绝设置 JAVA_25 级别（会直接崩启动）；mixin 类本身编译在 v69 没问题，被混入的 v69 目标类也照常处理 |
| 插件主类必须在提取前缀之外 | 前缀内的类会被父类加载器先加载，Paper 的 `JavaPlugin` 构造函数校验（须由 PluginClassLoader 创建）会拒绝 |

## 3. 最终形态

```
EntityCollisionOptimizer-Leaves.jar
├── leaves-plugin.json          （name/version/main/api-version/features.required=[mixin]/mixin{...}）
├── entity_collision_optimizer.mixins.json   （主配置，20 个 mixin，@env(PREINIT)，JAVA_21）
├── leaves_bootstrap.mixins.json             （引导配置：/eco 注册 + 停服清理）
├── org/edtp/entitycollisionoptimizer/**     （全部移植代码；入口类已剔除）
│   └── natives/macos-arm64/libEntityCollisionOptimizer.dylib
└── starm/eco/leaves/EcoLeavesPlugin.class   （休眠 Bukkit 主类，前缀之外）
```

- `leaves-plugin.json` 的 `mixin.package-name` = `org.edtp.entitycollisionoptimizer`（提取前缀，注入代码引用的所有类都必须在其中）
- 启动参数：`-Dleavesclip.enable.mixin=true`、`--enable-native-access=ALL-UNNAMED`

## 4. 实际适配清单（leaves-port 分支相对 origin/26.1）

### 4.1 基础设施（保持 Fabric 构建兼容）

| 文件 | 改动 |
| --- | --- |
| `EcoLog.java`（新增） | LOGGER 句柄从入口类拆出 |
| `FFMBackend.java` | LOGGER 引用改 EcoLog；native 查找改为候选列表（`/natives/...` 与 `/org/edtp/entitycollisionoptimizer/natives/...`）；资源读取类改为 `FFMBackend.class` |
| `entity_collision_optimizer.mixins.json` | 加 `"target": "@env(PREINIT)"`；minVersion 0.8.7；compat 降 JAVA_21 |

### 4.2 Paper/Moonrise 签名与结构分歧（实测逐个修复）

| 位置 | 分歧 | 处理 |
| --- | --- | --- |
| `EntityMixin#setRemoved` | Leaves 版多 `EntityRemoveEvent$Cause` 参数 | handler 增参 |
| `EntityMixin#setPosRaw` | 注入点只在 4 参重载（3 参仅委托） | 钉 `setPosRaw(DDDZ)V` |
| `EntityMixin#removePassenger` | 返回 boolean（vanilla void） | `CallbackInfoReturnable<Boolean>` + 钉单参描述符 |
| `ServerPlayerMixin#setGameMode` | 两个重载 | 钉 `(Lnet/minecraft/world/level/GameType;)Z` |
| `ServerLevelAccessor` / `PersistentEntitySectionManagerAccessor` | Moonrise 重写后 `ServerLevel` **无 `entityManager` 字段** | 整体删除；引导改用 `ServerLevel.getAllEntities()` |

### 4.3 Moonrise 实体查询适配（查询加速已恢复）

- 引擎漏斗已定位（@Mixin `EntityLookup`，共挂 5 处，全部 HEAD 取消 → 原生索引服务）：
  - **List 形（引擎主路径，Level#getEntities / Level#getEntitiesOfClass 直接调用）**：
    - `getEntities(Entity, AABB, List, Predicate)`（untyped）
    - `getEntities(EntityType, AABB, List, Predicate)`（exact-type；EntityType 本身即 EntityTypeTest）
    - `getEntities(Class, Entity, AABB, List, Predicate)`（class；`EntityTypeTest.forClass` 映射）
  - **LevelEntityGetter 兼容层**：`get(EntityTypeTest, AABB, AbortableIterationConsumer)`、`get(AABB, Consumer)`
  - maxCount 受限变体不挂钩（保持平台服务；`/ecoverify` 也用它作无污染基准）
- **恢复实现**：`MoonriseEntityQueryMixin`（`leaves/java`，priority 1100）；`CollisionFrame.engage/queriesEnabled`（ServerLevel 构造完成后才可服务，复刻旧版 attach 时机）；`QueryHookState` 计数与开关（`-Deco.queryHook=false` 可禁用）
- 删除死代码：原 `EntitySectionStorageMixin`/`EntitySectionStorageLevelBinding`（Moonrise 下 `EntitySectionStorage` 从不加载，实测该 mixin 从未应用）
- **顺序语义说明**：Moonrise 遍历 = region(z,x)→chunk(z,x)→section(y)→实体精确相交；vanilla = x→(y,z) section 序。两者**集合语义一致、跨区块顺序可能不同**；原生索引按 mod 契约保持 vanilla 序（与插件已生效的推动/移动路径同一取向）。用 `/ecoverify` 实测集合一致性并报告顺序差异
- 追踪回调仍然有效（`ServerLevel$EntityCallbacks` 是 Moonrise `ServerEntityLookup` 的 LevelCallback）→ native 索引增删改同步正常

### 4.4 新增文件（leaves/ 目录，Fabric 构建不受影响）

- `leaves/java/starm/eco/leaves/EcoLeavesPlugin.java`（休眠主类）
- `leaves/java/org/edtp/entitycollisionoptimizer/leaves/mixin/LeavesCommandsMixin.java`（`Commands` 构造后注册 `/eco`，幂等保护）
- `leaves/java/org/edtp/entitycollisionoptimizer/leaves/mixin/LeavesLifecycleMixin.java`（`stopServer` HEAD 清理 native 状态）
- `leaves/resources/{leaves-plugin.json, leaves_bootstrap.mixins.json}`

## 5. 构建与测试

```bash
# 构建（javac release 25 对运行时 jar；clang 直编 darwin-arm64；打包含 natives 重排）
zsh eco-leaves-port/leaves/build-plugin.sh

# 安装：复制 dist/EntityCollisionOptimizer-Leaves.jar 到服务器 plugins/
# 启动参数（写入启动脚本）：
#   -Dleavesclip.enable.mixin=true
#   --enable-native-access=ALL-UNNAMED

# 已验证的测试脚本
zsh eco-leaves-port/leaves/run-m2-test.sh     # 启动 + /eco + FFM 证据
zsh eco-leaves-port/leaves/run-m3-stress.sh   # 200 实体压测（带清理）
zsh eco-leaves-port/leaves/run-r2-test.sh     # 查询钩子校验（60 实体 + /ecoverify，带清理）

# 服务器内诊断命令
# /ecoverify  —— 对比 native 结果与 Moonrise 遍历：setMismatch 必须为 0；
#                 orderDiff 为信息性（跨区块顺序差异，原生保持 vanilla 序）；
#                 servedQueries 为钩子命中计数
```

## 6. 验证记录

| 阶段 | 结果 |
| --- | --- |
| M1 最小冒烟 | 提取 ✓ / 配置注册 ✓ / 普通 Mixin ✓ / MixinExtras ✓ / 插件加载 ✓ |
| M2 端到端 | 启动 ✓；49 次 mixin 应用；`Initialized 1 plugin`；native 提取+加载 ✓；`/eco → FFM initialized=true`；停服 ✓ |
| M3 压测 | 运行中（200 实体堆积推挤 + 清理） |

## 7. 已知限制与后续

1. **查询顺序语义**（Moonrise，见 4.3）——集合一致性由 `/ecoverify` 实测保证；跨区块返回顺序为 vanilla 序（mod 契约），与 Moonrise 原生顺序可能不同，如需完全跟随平台顺序可用 `-Deco.queryHook=false` 关闭钩子
2. **leavesclip mixin 为实验特性**——服务器升级 Leaves 版本后需重跑 M2 冒烟
3. **本分支与 Fabric 主线的同步**：`leaves-port` 分支对共享文件做了 Paper 化适配（4.2），从 `origin/26.1` 合并时注意这 5 处冲突
4. **分发**（若公开）：文档注明启动参数；GPL 合规三件套（源码/声明/修改说明）
5. 其它 native 平台（Linux x64 等）如需分发，沿用 `native/` 源码 + 对应 `AR_*` 宏编译即可
