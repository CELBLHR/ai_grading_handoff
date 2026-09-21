# AI Grading Handoff

本仓库保存 AI 批改方法包的可交付源码和自动化测试。

## 版本管理

- `main` 始终对应已经通过测试、可以交付的稳定代码。
- 功能和修复在短期分支中开发，例如 `feature/question-generation`、`fix/latex-output`。
- 正式版本使用 Git 标签标记，例如 `v2.6.0`、`v2.6.1`。
- `.env`、真实学生数据、实验输出和 ZIP 包不提交到仓库。

## 本地测试

```powershell
python -m unittest discover -s test
```

更完整的调用说明见 `ai_grading_service/README.md`。
