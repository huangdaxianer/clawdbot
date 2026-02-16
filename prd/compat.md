# Compat 模块 - 产品需求文档 (PRD)

## 1. 模块概述与用途

`compat` 模块（兼容性模块）定义了项目的名称常量和遗留名称映射。用于项目从旧名称迁移到当前名称 `"openclaw"` 时的向后兼容支持。该模块提供项目名称、清单键名、插件文件名、Canvas 处理器名称以及 macOS 应用源码目录路径等常量。

目前所有遗留名称列表均为空数组，表示当前没有需要支持的历史遗留名称。

## 2. 目录结构

```
src/compat/
└── legacy-names.ts                # 项目名称与遗留兼容常量
```

## 3. 文件详细说明

---

### 3.1 `legacy-names.ts`

#### 导入

无外部导入。

#### 导出常量

| 常量名 | 值 | 类型 | 用途 |
|--------|----|------|------|
| `PROJECT_NAME` | `"openclaw"` | `"openclaw"`（字面量类型，`as const`） | 当前项目名称 |
| `LEGACY_PROJECT_NAMES` | `[]` | `readonly []`（空只读元组，`as const`） | 历史遗留项目名称列表 |
| `MANIFEST_KEY` | `PROJECT_NAME`（即 `"openclaw"`） | `"openclaw"` | 当前清单（manifest）键名 |
| `LEGACY_MANIFEST_KEYS` | `LEGACY_PROJECT_NAMES`（即 `[]`） | `readonly []` | 历史遗留清单键名列表 |
| `LEGACY_PLUGIN_MANIFEST_FILENAMES` | `[]` | `readonly []`（`as const`） | 历史遗留插件清单文件名列表 |
| `LEGACY_CANVAS_HANDLER_NAMES` | `[]` | `readonly []`（`as const`） | 历史遗留 Canvas 处理器名称列表 |
| `MACOS_APP_SOURCES_DIR` | `"apps/macos/Sources/OpenClaw"` | `"apps/macos/Sources/OpenClaw"`（字面量类型，`as const`） | macOS 应用源码目录路径 |
| `LEGACY_MACOS_APP_SOURCES_DIRS` | `[]` | `readonly []`（`as const`） | 历史遗留 macOS 应用源码目录列表 |

#### 完整源码

```typescript
export const PROJECT_NAME = "openclaw" as const;

export const LEGACY_PROJECT_NAMES = [] as const;

export const MANIFEST_KEY = PROJECT_NAME;

export const LEGACY_MANIFEST_KEYS = LEGACY_PROJECT_NAMES;

export const LEGACY_PLUGIN_MANIFEST_FILENAMES = [] as const;

export const LEGACY_CANVAS_HANDLER_NAMES = [] as const;

export const MACOS_APP_SOURCES_DIR = "apps/macos/Sources/OpenClaw" as const;

export const LEGACY_MACOS_APP_SOURCES_DIRS = [] as const;
```

## 4. 文件间依赖关系

```
legacy-names.ts
  └─ （无外部依赖）
```

该文件为纯常量导出，不依赖任何外部模块。它作为其他模块的依赖被引用，提供统一的项目名称和兼容性常量。

## 5. 整体架构

`compat` 模块是一个极简的常量定义模块，采用集中化的名称管理策略：

- **当前名称**：通过 `PROJECT_NAME`、`MANIFEST_KEY`、`MACOS_APP_SOURCES_DIR` 定义
- **遗留名称**：通过 `LEGACY_*` 系列常量定义，当前全部为空数组
- **类型安全**：所有常量使用 `as const` 断言，确保类型为字面量类型或只读元组
- **派生关系**：`MANIFEST_KEY` 直接引用 `PROJECT_NAME`，`LEGACY_MANIFEST_KEYS` 直接引用 `LEGACY_PROJECT_NAMES`，确保一致性

该模块的设计便于将来项目重命名时，只需将当前名称添加到对应的 `LEGACY_*` 数组中，并更新当前名称常量即可实现平滑迁移。
