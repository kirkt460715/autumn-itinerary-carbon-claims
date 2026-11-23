"""短途研学低碳标签工作流。

仅依赖 Python 标准库。模块划分：

- model: 源表加载、类型化记录与引用完整性校验
- factors: 因子选择与一轮比较内的因子钉版
- accounting: 车段/车次排放、合乘与空驶分摊、每儿童份额、不确定来源
- benchmark: 基准排放构建、同等服务口径门槛、共享车次一致性
- review: 标签判定、变更通知影响、卡片复核与专题合计
- workflow: 按 as-of 时点编排整轮复核并产出可回查的结果
"""

__all__ = ["model", "factors", "accounting", "benchmark", "review", "workflow"]
