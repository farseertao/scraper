# Zhejiang Achievement Crawler

This project crawls a fixed set of Zhejiang university and lab sources and keeps
only achievement-related raw documents for downstream review.

## Quick start

```powershell
conda activate scraper
pip install -r requirements.txt
Copy-Item .env.example .env
$env:PYTHONPATH='src'
python -m zj_agent.cli init-db
python -m zj_agent.cli seed-sources
python -m zj_agent.cli crawl inspect --source zju_itt
python -m zj_agent.cli crawl bootstrap --all
python -m zj_agent.cli crawl weekly --all
```
