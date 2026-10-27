# prj_chart

折线图，支持选择时间段查看QPS数据
## 环境

- Python 3.7
- Django 2.2

## 快速开始

# 安装依赖
pip install django

# 初始化数据库
python manage.py migrate

# 生成模拟数据（可选）
python manage.py seed_qps

# 启动服务
python manage.py runserver 0.0.0.0:8000


## 访问

- 图表页面：http://127.0.0.1:8000/qps/
- 数据接口：http://127.0.0.1:8000/api/qps/
- 后台管理：http://127.0.0.1:8000/admin/
###