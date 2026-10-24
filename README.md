# AI 성공 예측기 (AI Predictor)

> [MiroFish](https://github.com/666ghj/MiroFish)를 포크해 **신제품·신사업·신전략의 성공 가능성**을 예측하도록 바꾼 버전입니다.
> 대상 사용자는 한국의 기업과 예비 창업자입니다. 원본 MiroFish 문서는 아래에 그대로 두었습니다.

## 무엇이 달라졌나

| 영역 | 원본 MiroFish | AI 성공 예측기 |
|---|---|---|
| 입력 | 자료 + 자연어 질문 | 자료 + **사업 브리프**: 유형, 추진 주체(예비창업자~대기업), 타깃 고객, 가격, 경쟁사, 채널, KPI |
| 그래프 | 여론 시뮬레이션용 엔티티 | **시장 이해관계자** 중심: 고객 세그먼트, 경쟁사, 유통·파트너, 언론, 규제기관, 투자자 |
| Agent | 문서에서 추출한 엔티티만 | 엔티티 + **한국인 가상 소비자 패널**: [Nemotron-Personas-Korea](https://huggingface.co/datasets/nvidia/Nemotron-Personas-Korea)에서 타깃 고객에 맞춰 선발 |
| 결과 | 서술형 예측 보고서 | 보고서 맨 앞에 **성공 예측 스코어카드**: 점수, 추진·조건부·피벗·중단 판정, 세그먼트별 수용도, 지원사업·규제 점검 |
| 맥락 | 중국 기준(시간대 UTC+8, 중국어) | **한국 기준**: KST, 한국어 UI, 한국 시장·정책·규제 지식팩 |

## 동작 흐름

1. **브리프와 자료 입력**: 사업계획서, 시장조사 등 PDF/MD/TXT 파일과 사업 브리프를 넣습니다.
2. **이해관계자 그래프 구축**: 고객 세그먼트, 경쟁사, 파트너, 언론, 규제기관을 엔티티로 추출합니다.
3. **Agent 구성**: 그래프 엔티티에 한국인 소비자 패널(기본 30명)을 더합니다. 패널은 LLM이 브리프의 타깃 고객을 나이·성별·지역·직업 조건으로 바꾼 뒤 그 조건으로 선발합니다.
4. **시뮬레이션**: Twitter·Reddit형 가상 SNS에서 출시 반응이 퍼지는 과정을 시뮬레이션합니다.
5. **성공 예측 설문**: 시뮬레이션이 끝나면 모든 Agent가 구조화 설문에 답합니다. 문항은 구매·도입 의향(1~5), 지불 의향 가격, 추천 의향(0~10), 입장(-2~2), 이유, 우려입니다.
6. **스코어카드와 보고서**: 설문을 점수로 집계하고, ReportAgent가 한국 시장·정책 맥락을 반영해 보고서를 씁니다.

## 성공 점수 산식 (`backend/app/services/success_evaluator.py`)

| 구성 요소 | 계산 | 가중치 |
|---|---|---|
| 수요 | 고객 응답자의 평균 구매의향을 0~100으로 환산 | 40% |
| 가격 적합도 | 제시가 이상 지불 의향 비율 | 20% |
| 추천 의향 | NPS를 0~100으로 환산 | 20% |
| 생태계 지지 | 고객·경쟁사를 뺀 이해관계자(파트너, 언론, 규제기관 등)의 평균 입장 | 20% |

- 값이 없는 구성 요소는 빼고, 나머지로 가중치를 다시 나눕니다.
- 판정 기준:
  - 70점 이상: 추진(Go)
  - 55점 이상: 조건부 추진
  - 40점 이상: 피벗 검토
  - 40점 미만: 중단 권고
- 추정 구매 전환율 = top-box 가중치(5점 75%, 4점 25%, 3점 5%) × LLM 응답자 낙관 편향 보정 0.7

> ⚠️ 가중치와 임계값은 **실제 출시 결과로 보정되지 않은 기본값**입니다. 점수는 상대 비교와 의사결정 참고용입니다.
> 실제 성과 데이터가 쌓이면 이 값부터 보정하세요.

## 빠른 시작

```bash
cp .env.example .env          # LLM_API_KEY, ZEP_API_KEY 입력
npm run setup:all             # 의존성 설치
npm run setup:personas        # 한국인 페르소나 표본 5,000명 다운로드 (backend/data/, 약 1~2분)
npm run dev                   # 프론트 http://localhost:3000, 백엔드 http://localhost:5001
```

- 페르소나 표본이 없으면 소비자 패널 없이 동작합니다.
- 패널 크기는 `KOREAN_PANEL_SIZE`로 조정하고, 0으로 두면 패널을 끕니다.
- Docker: `docker compose up -d --build`. 이 저장소 소스로 이미지를 빌드합니다.

## 주요 API (추가분)

- `POST /api/graph/ontology/generate`: 기존 필드에 더해 `business_brief`(JSON 문자열)를 받습니다.
- `POST /api/report/generate`: 보고서 생성 전에 성공 예측 설문을 자동으로 실행합니다. 시뮬레이션 환경이 살아 있어야 합니다.
- `GET /api/report/success-evaluation/<simulation_id>`: 스코어카드를 조회합니다.
- `POST /api/report/success-evaluation`: 설문을 다시 실행합니다(비동기). 요청 본문은 `{"simulation_id": "..."}`입니다.

## ceoai.kr 크레딧 결제 연동

예측은 [ceoai.kr/predictor](https://ceoai.kr/predictor/)에서 유료로 시작합니다. 결제 흐름은 다음과 같습니다.

1. 회원이 ceoai.kr에서 Stripe 카드 결제로 크레딧을 충전합니다.
2. 「예측 시작」을 누르면 예측 1건 값만큼 크레딧이 차감되고, 서명된 **실행권**이 발급됩니다. 실행권은 하루 동안 한 번 쓸 수 있습니다.
3. 회원은 `?ticket=`을 단 주소로 이 앱에 들어옵니다.
4. 앱은 실행권을 확인하고, 예측 프로젝트를 만들 때 소진합니다. 같은 실행권으로는 두 번 만들 수 없습니다.
5. 결과에 따라 ceoai.kr에 알립니다(`POST /api/predictor-callback`).
   - 프로젝트를 만들다 실패하면(문서 해석 실패, 온톨로지 생성 실패) `refund`를 보내고, 크레딧이 자동으로 환불됩니다.
   - 첫 보고서가 완성되면 `complete`를 보냅니다. 그 뒤로는 환불되지 않습니다.
   - 그래프·시뮬레이션·보고서 단계에서 실패한 경우에는 같은 프로젝트에서 추가 비용 없이 다시 시도할 수 있습니다.

| 환경 변수 | 설명 |
|---|---|
| `PREDICTOR_TICKET_SECRET` | ceoai.kr의 같은 이름 시크릿과 **같은 값**입니다. 비워 두면 실행권 없이 열립니다(로컬 개발용). |
| `PREDICTOR_CALLBACK_URL` | 기본값은 `https://ceoai.kr/api/predictor-callback`입니다. |

실행권과 콜백 서명 형식은 `backend/app/utils/predictor_ticket.py`에 있습니다. ceoai.kr `functions/_predictor.js`와 같아야 하며, `tests/test_predictor_ticket.py`가 ceoai.kr이 만든 실제 실행권으로 이를 확인합니다.

## 한국 시장·정책 지식팩

`backend/app/knowledge/korea_business_context.md`는 보고서와 평가 프롬프트에 들어가는 요약 자료입니다. 다루는 내용은 다음과 같습니다.

- 2026년 창업지원사업, TIPS, 정책자금·보증·세제
- 규제 샌드박스, 업종별 인허가
- 한국 시장 특성

지원사업의 금액·일정은 매년 바뀝니다. **K-Startup·중기부 공고를 기준으로 주기적으로 갱신하세요.**

## 출처와 라이선스

- 원본: [666ghj/MiroFish](https://github.com/666ghj/MiroFish) (AGPL-3.0). 이 포크도 AGPL-3.0입니다. 수정본을 네트워크 서비스로 제공하면 사용자에게 소스를 공개해야 합니다.
- 페르소나 데이터: NVIDIA, *Nemotron-Personas-Korea* (CC BY 4.0). 표본 파일은 저장소에 포함하지 않으며, 사용자가 직접 내려받습니다.
- 시뮬레이션 엔진: [CAMEL-AI OASIS](https://github.com/camel-ai/oasis)

---

<div align="center">

<img src="./static/image/MiroFish_logo_compressed.jpeg" alt="MiroFish Logo" width="75%"/>

<a href="https://trendshift.io/repositories/16144" target="_blank"><img src="https://trendshift.io/api/badge/repositories/16144" alt="666ghj%2FMiroFish | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

简洁通用的群体智能引擎，预测万物
</br>
<em>A Simple and Universal Swarm Intelligence Engine, Predicting Anything</em>

<a href="https://www.shanda.com/" target="_blank"><img src="./static/image/shanda_logo.png" alt="666ghj%2FMiroFish | Shanda" height="40"/></a>

[![GitHub Stars](https://img.shields.io/github/stars/666ghj/MiroFish?style=flat-square&color=DAA520)](https://github.com/666ghj/MiroFish/stargazers)
[![GitHub Watchers](https://img.shields.io/github/watchers/666ghj/MiroFish?style=flat-square)](https://github.com/666ghj/MiroFish/watchers)
[![GitHub Forks](https://img.shields.io/github/forks/666ghj/MiroFish?style=flat-square)](https://github.com/666ghj/MiroFish/network)
[![Docker](https://img.shields.io/badge/Docker-Build-2496ED?style=flat-square&logo=docker&logoColor=white)](https://hub.docker.com/)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/666ghj/MiroFish)

[![Discord](https://img.shields.io/badge/Discord-Join-5865F2?style=flat-square&logo=discord&logoColor=white)](http://discord.gg/ePf5aPaHnA)
[![X](https://img.shields.io/badge/X-Follow-000000?style=flat-square&logo=x&logoColor=white)](https://x.com/mirofish_ai)
[![Instagram](https://img.shields.io/badge/Instagram-Follow-E4405F?style=flat-square&logo=instagram&logoColor=white)](https://www.instagram.com/mirofish_ai/)

[English](./README.md) | [中文文档](./README-ZH.md)

</div>

## ⚡ Overview

**MiroFish** is a next-generation AI prediction engine powered by multi-agent technology. By extracting seed information from the real world (such as breaking news, policy drafts, or financial signals), it automatically constructs a high-fidelity parallel digital world. Within this space, thousands of intelligent agents with independent personalities, long-term memory, and behavioral logic freely interact and undergo social evolution. You can inject variables dynamically from a "God's-eye view" to precisely deduce future trajectories — **rehearse the future in a digital sandbox, and win decisions after countless simulations**.

> You only need to: Upload seed materials (data analysis reports or interesting novel stories) and describe your prediction requirements in natural language</br>
> MiroFish will return: A detailed prediction report and a deeply interactive high-fidelity digital world

### Our Vision

MiroFish is dedicated to creating a swarm intelligence mirror that maps reality. By capturing the collective emergence triggered by individual interactions, we break through the limitations of traditional prediction:

- **At the Macro Level**: We are a rehearsal laboratory for decision-makers, allowing policies and public relations to be tested at zero risk
- **At the Micro Level**: We are a creative sandbox for individual users — whether deducing novel endings or exploring imaginative scenarios, everything can be fun, playful, and accessible

From serious predictions to playful simulations, we let every "what if" see its outcome, making it possible to predict anything.

## 🌐 Live Demo

Welcome to visit our online demo environment and experience a prediction simulation on trending public opinion events we've prepared for you: [mirofish-live-demo](https://666ghj.github.io/mirofish-demo/)

## 📸 Screenshots

<div align="center">
<table>
<tr>
<td><img src="./static/image/Screenshot/运行截图1.png" alt="Screenshot 1" width="100%"/></td>
<td><img src="./static/image/Screenshot/运行截图2.png" alt="Screenshot 2" width="100%"/></td>
</tr>
<tr>
<td><img src="./static/image/Screenshot/运行截图3.png" alt="Screenshot 3" width="100%"/></td>
<td><img src="./static/image/Screenshot/运行截图4.png" alt="Screenshot 4" width="100%"/></td>
</tr>
<tr>
<td><img src="./static/image/Screenshot/运行截图5.png" alt="Screenshot 5" width="100%"/></td>
<td><img src="./static/image/Screenshot/运行截图6.png" alt="Screenshot 6" width="100%"/></td>
</tr>
</table>
</div>

## 🎬 Demo Videos

### 1. Wuhan University Public Opinion Simulation + MiroFish Project Introduction

<div align="center">
<a href="https://www.bilibili.com/video/BV1VYBsBHEMY/" target="_blank"><img src="./static/image/武大模拟演示封面.png" alt="MiroFish Demo Video" width="75%"/></a>

Click the image to watch the complete demo video for prediction using BettaFish-generated "Wuhan University Public Opinion Report"
</div>

### 2. Dream of the Red Chamber Lost Ending Simulation

<div align="center">
<a href="https://www.bilibili.com/video/BV1cPk3BBExq" target="_blank"><img src="./static/image/红楼梦模拟推演封面.jpg" alt="MiroFish Demo Video" width="75%"/></a>

Click the image to watch MiroFish's deep prediction of the lost ending based on hundreds of thousands of words from the first 80 chapters of "Dream of the Red Chamber"
</div>

> **Financial Prediction**, **Political News Prediction** and more examples coming soon...

## 🔄 Workflow

1. **Graph Building**: Seed extraction & Individual/collective memory injection & GraphRAG construction
2. **Environment Setup**: Entity relationship extraction & Persona generation & Agent configuration injection
3. **Simulation**: Dual-platform parallel simulation & Auto-parse prediction requirements & Dynamic temporal memory updates
4. **Report Generation**: ReportAgent with rich toolset for deep interaction with post-simulation environment
5. **Deep Interaction**: Chat with any agent in the simulated world & Interact with ReportAgent

## 🚀 Quick Start

### Option 1: Source Code Deployment (Recommended)

#### Prerequisites

| Tool | Version | Description | Check Installation |
|------|---------|-------------|-------------------|
| **Node.js** | 18+ | Frontend runtime, includes npm | `node -v` |
| **Python** | ≥3.11, ≤3.12 | Backend runtime | `python --version` |
| **uv** | Latest | Python package manager | `uv --version` |

#### 1. Configure Environment Variables

```bash
# Copy the example configuration file
cp .env.example .env

# Edit the .env file and fill in the required API keys
```

**Required Environment Variables:**

```env
# LLM API Configuration (supports any LLM API with OpenAI SDK format)
# Recommended: Alibaba Qwen-plus model via Bailian Platform: https://bailian.console.aliyun.com/
# High consumption, try simulations with fewer than 40 rounds first
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL_NAME=qwen-plus

# Zep Cloud Configuration
# Free monthly quota is sufficient for simple usage: https://app.getzep.com/
ZEP_API_KEY=your_zep_api_key
```

#### 2. Install Dependencies

```bash
# One-click installation of all dependencies (root + frontend + backend)
npm run setup:all
```

Or install step by step:

```bash
# Install Node dependencies (root + frontend)
npm run setup

# Install Python dependencies (backend, auto-creates virtual environment)
npm run setup:backend
```

#### 3. Start Services

```bash
# Start both frontend and backend (run from project root)
npm run dev
```

**Service URLs:**
- Frontend: `http://localhost:3000`
- Backend API: `http://localhost:5001`

**Start Individually:**

```bash
npm run backend   # Start backend only
npm run frontend  # Start frontend only
```

### Option 2: Docker Deployment

```bash
# 1. Configure environment variables (same as source deployment)
cp .env.example .env

# 2. Pull image and start
docker compose up -d
```

Reads `.env` from root directory by default, maps ports `3000 (frontend) / 5001 (backend)`

> Mirror address for faster pulling is provided as comments in `docker-compose.yml`, replace if needed.

## 📬 Join the Conversation

<div align="center">
<img src="./static/image/QQ群.png" alt="QQ Group" width="60%"/>
</div>

&nbsp;

The MiroFish team is recruiting full-time/internship positions. If you're interested in multi-agent simulation and LLM applications, feel free to send your resume to: **mirofish@shanda.com**

## 📄 Acknowledgments

**MiroFish has received strategic support and incubation from Shanda Group!**

MiroFish's simulation engine is powered by **[OASIS (Open Agent Social Interaction Simulations)](https://github.com/camel-ai/oasis)**, We sincerely thank the CAMEL-AI team for their open-source contributions!

## 📈 Project Statistics

<a href="https://github.com/666ghj/MiroFish">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="static/image/star-history-dark.svg" />
   <source media="(prefers-color-scheme: light)" srcset="static/image/star-history-light.svg" />
   <img alt="666ghj/MiroFish Star History Chart" src="static/image/star-history-light.svg" />
 </picture>
</a>
