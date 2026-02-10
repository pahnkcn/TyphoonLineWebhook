# Multi-AI Consensus System Diagram

## 1. ระบบเดิม (Single AI — Grok Only)

```mermaid
sequenceDiagram
    participant U as ผู้ใช้ (LINE)
    participant W as Webhook Server
    participant DB as Chat History DB
    participant G as Grok API

    U->>W: ส่งข้อความ
    W->>DB: ดึงประวัติแชท
    DB-->>W: messages[]
    W->>W: สร้าง api_messages<br/>(system + history + user msg)
    W->>G: chat.completions.create()
    G-->>W: คำตอบ
    W->>DB: บันทึกประวัติ
    W->>U: ส่งคำตอบกลับ
```

## 2. ระบบใหม่ (Multi-AI Consensus)

```mermaid
sequenceDiagram
    participant U as ผู้ใช้ (LINE)
    participant W as Webhook Server
    participant DB as Chat History DB
    participant MAI as Multi-AI Engine
    participant G as Grok API
    participant GM as Gemini API
    participant DS as DeepSeek API
    participant LOG as multi_ai_logs

    U->>W: ส่งข้อความ
    W->>DB: ดึงประวัติแชท
    DB-->>W: messages[]
    W->>W: สร้าง api_messages<br/>(system + history + user msg)
    W->>MAI: multi_ai_chat(api_messages)

    Note over MAI: ═══ Phase 1: Generation (Parallel) ═══

    par Generate พร้อมกัน
        MAI->>G: chat.completions.create(messages)
        G-->>MAI: คำตอบ A (13.7s)
    and
        MAI->>GM: chat.completions.create(messages)
        GM-->>MAI: คำตอบ B (4.9s)
    and
        MAI->>DS: chat.completions.create(messages)
        DS-->>MAI: คำตอบ C (3.5s)
    end

    Note over MAI: ⏳ Cooldown 1.5s (ป้องกัน rate limit)

    Note over MAI: ═══ Phase 2: Cross-Evaluation (Parallel) ═══

    par Evaluate พร้อมกัน
        MAI->>G: ให้คะแนน B, C<br/>(+ บทบาทแชทบอท + คำถามผู้ใช้)
        G-->>MAI: {"B": 90, "C": 85}
    and
        MAI->>GM: ให้คะแนน A, C<br/>(+ บทบาทแชทบอท + คำถามผู้ใช้)
        GM-->>MAI: {"A": 88, "C": 82}
    and
        MAI->>DS: ให้คะแนน A, B<br/>(+ บทบาทแชทบอท + คำถามผู้ใช้)
        DS-->>MAI: {"A": 85, "B": 92}
    end

    Note over MAI: ═══ Phase 3: Aggregate & Select ═══

    MAI->>MAI: คำนวณคะแนนเฉลี่ย<br/>A(Grok)=86.5 | B(Gemini)=91.0 | C(DeepSeek)=83.5
    MAI->>MAI: เลือก B (Gemini) คะแนนสูงสุด

    MAI-->>W: ConsensusResult (best=Gemini)
    W->>LOG: บันทึก scores, times, tokens
    W->>DB: บันทึกประวัติ
    W->>U: ส่งคำตอบที่ดีที่สุดกลับ
```

## 3. สถาปัตยกรรมภาพรวม (Architecture)

```mermaid
flowchart TB
    subgraph LINE["LINE Platform"]
        USER[ผู้ใช้]
    end

    subgraph SERVER["Webhook Server (Flask)"]
        WH[Webhook Handler]
        GEN[generate_ai_response_with_timeout]

        subgraph SINGLE["โหมดเดิม (Single AI)"]
            GROK_ONLY[Grok Client]
        end

        subgraph MULTI["โหมดใหม่ (Multi-AI Consensus)"]
            direction TB
            P1["Phase 1: generate_all()
            ส่งข้อความไปทุกโมเดลพร้อมกัน
            (ThreadPoolExecutor)"]

            CD["⏳ Cooldown 1.5s"]

            P2["Phase 2: cross_evaluate()
            ทุกโมเดลให้คะแนนกันข้าม
            + เห็นบทบาทแชทบอท"]

            P3["Phase 3: aggregate_scores()
            + select_best()
            เลือกคำตอบที่ดีที่สุด"]

            P1 --> CD --> P2 --> P3
        end
    end

    subgraph PROVIDERS["AI Providers"]
        GROK["Grok (x.ai)"]
        GEMINI["Gemini (Google)"]
        DEEPSEEK["DeepSeek"]
        OR["OpenRouter (optional)"]
    end

    subgraph STORAGE["Storage"]
        MYSQL[(MySQL
        chat_logs
        multi_ai_logs)]
        REDIS[(Redis
        sessions)]
    end

    subgraph DASH["Dashboard"]
        API["/api/dashboard/multi-ai-stats"]
        UI["Chart.js Visualizations
        - Wins per model
        - Avg scores
        - Token usage
        - Per-model gen/eval times"]
    end

    USER <-->|ข้อความ| WH
    WH --> GEN
    GEN -->|MULTI_AI_ENABLED=false| SINGLE
    GEN -->|MULTI_AI_ENABLED=true| MULTI
    SINGLE --> GROK
    MULTI --> GROK & GEMINI & DEEPSEEK & OR
    GEN --> MYSQL & REDIS
    MYSQL --> API --> UI
```

## 4. เปรียบเทียบ (Comparison)

```mermaid
flowchart LR
    subgraph OLD["ระบบเดิม (Single AI)"]
        direction TB
        O1["1 โมเดล (Grok)"] --> O2["1 คำตอบ"] --> O3["ส่งกลับเลย"]
        O4["⏱ ~3-14 วินาที"]
        O5["❌ ไม่มีการตรวจสอบคุณภาพ"]
        O6["❌ ถ้า API ล่ม = ไม่มีคำตอบ"]
    end

    subgraph NEW["ระบบใหม่ (Multi-AI Consensus)"]
        direction TB
        N1["3+ โมเดล (พร้อมกัน)"] --> N2["3+ คำตอบ"] --> N3["ให้คะแนนกันข้าม"]
        N3 --> N4["เลือกคำตอบที่ดีที่สุด"]
        N5["⏱ ~15-30 วินาที"]
        N6["✅ ตรวจสอบคุณภาพอัตโนมัติ"]
        N7["✅ Fallback ถ้าบาง API ล่ม"]
        N8["✅ Evaluator เห็นบทบาทแชทบอท"]
    end

    OLD -.->|"MULTI_AI_ENABLED=true"| NEW
```

## 5. Evaluation Prompt (สิ่งที่ Evaluator เห็น)

```mermaid
flowchart TB
    subgraph EVAL_OLD["ก่อนแก้ไข"]
        direction TB
        EO1["คำถามผู้ใช้ ✅"]
        EO2["คำตอบ A, B (ซ่อนชื่อ) ✅"]
        EO3["บทบาทแชทบอท ❌"]
        EO4["ประวัติแชท ❌"]
    end

    subgraph EVAL_NEW["หลังแก้ไข"]
        direction TB
        EN1["คำถามผู้ใช้ ✅"]
        EN2["คำตอบ A, B (ซ่อนชื่อ) ✅"]
        EN3["บทบาทแชทบอท ✅"]
        EN4["ประวัติแชท ❌ (ไม่ต้อง)"]
    end

    EVAL_OLD -->|"ปรับปรุง"| EVAL_NEW
```

## 6. Flowchart แบบ Icon (เข้าใจง่าย)

### 6.1 ระบบเดิม (Single AI)

```mermaid
flowchart TD
    START(("🟢 เริ่ม"))
    MSG["📱 ผู้ใช้ส่งข้อความผ่าน LINE"]
    HIST["🗂️ ดึงประวัติแชทจาก DB"]
    BUILD["📝 ประกอบ messages\n🤖 System Prompt\n💬 ประวัติแชท\n👤 ข้อความล่าสุด"]
    CALL["🧠 ส่งไป Grok API\n⏱️ รอ ~3-14 วินาที"]
    FAIL{{"❌ API ล่ม?"}}
    ERR["🚨 แสดงข้อความ Error"]
    SAVE["💾 บันทึกประวัติลง DB"]
    REPLY["📤 ส่งคำตอบกลับ LINE"]
    DONE(("🔴 จบ"))

    START --> MSG --> HIST --> BUILD --> CALL
    CALL --> FAIL
    FAIL -->|ใช่| ERR --> DONE
    FAIL -->|ไม่| SAVE --> REPLY --> DONE

    style START fill:#22c55e,color:#fff
    style DONE fill:#ef4444,color:#fff
    style MSG fill:#6366f1,color:#fff
    style CALL fill:#f97316,color:#fff
    style ERR fill:#ef4444,color:#fff
    style REPLY fill:#0ea5e9,color:#fff
    style SAVE fill:#14b8a6,color:#fff
```

### 6.2 ระบบใหม่ (Multi-AI Consensus)

```mermaid
flowchart LR
    START(("🟢\nเริ่ม"))
    MSG["📱\nผู้ใช้ส่งข้อความ\nผ่าน LINE"]
    HIST["🗂️\nดึงประวัติแชท\nจาก DB"]
    BUILD["📝\nประกอบ messages\n🤖 System Prompt\n💬 ประวัติ\n👤 ข้อความ"]
    CHECK{{"⚙️\nMULTI_AI\nENABLED?"}}

    subgraph OLD_PATH["โหมดเดิม"]
        GROK_SINGLE["🧠 Grok API\nเท่านั้น"]
    end

    subgraph PHASE1["⚡ Phase 1 — Generation ขนานกัน"]
        direction TB
        G1["🔴 Grok\n⏱️ 13.7s\n📊 1,943 tok"]
        G2["🔵 Gemini\n⏱️ 4.9s\n📊 1,225 tok"]
        G3["🟢 DeepSeek\n⏱️ 3.5s\n📊 848 tok"]
    end

    COOL["⏳\nCooldown\n1.5s\n🛡️ Rate Limit"]

    subgraph PHASE2["🔍 Phase 2 — Cross-Evaluation ขนานกัน"]
        direction TB
        E1["🔴 Grok ให้คะแนน\n🔵 Gemini & 🟢 DeepSeek"]
        E2["🔵 Gemini ให้คะแนน\n🔴 Grok & 🟢 DeepSeek"]
        E3["🟢 DeepSeek ให้คะแนน\n🔴 Grok & 🔵 Gemini"]
    end

    CONTEXT["📋 Evaluator เห็น:\n🤖 บทบาทแชทบอท\n👤 คำถามผู้ใช้\n📄 คำตอบ A, B ซ่อนชื่อ"]

    subgraph PHASE3["🏆 Phase 3 — เลือกคำตอบ"]
        direction TB
        AGG["📊 คะแนนเฉลี่ย\n🔴 Grok = 86.5\n🔵 Gemini = 91.0 ⭐\n🟢 DeepSeek = 83.5"]
        BEST["🥇 เลือก Gemini\nคะแนนสูงสุด!"]
        AGG --> BEST
    end

    LOG["📈\nบันทึก Dashboard\n⏱️ เวลา\n📊 คะแนน & Tokens"]
    SAVE["💾\nบันทึก DB"]
    REPLY["📤\nส่งคำตอบ\nกลับ LINE"]
    DONE(("🔴\nจบ"))

    START --> MSG --> HIST --> BUILD --> CHECK
    CHECK -->|false| OLD_PATH --> SAVE
    CHECK -->|true| PHASE1
    PHASE1 --> COOL --> PHASE2
    CONTEXT -.-> PHASE2
    PHASE2 --> PHASE3
    PHASE3 --> LOG --> SAVE --> REPLY --> DONE

    style START fill:#22c55e,color:#fff
    style DONE fill:#ef4444,color:#fff
    style MSG fill:#6366f1,color:#fff
    style CHECK fill:#f59e0b,color:#fff
    style COOL fill:#fbbf24,color:#000
    style BEST fill:#22c55e,color:#fff
    style REPLY fill:#0ea5e9,color:#fff
    style LOG fill:#8b5cf6,color:#fff
    style SAVE fill:#14b8a6,color:#fff
    style CONTEXT fill:#e0e7ff,color:#333,stroke:#6366f1,stroke-dasharray:5
    style G1 fill:#ef4444,color:#fff
    style G2 fill:#3b82f6,color:#fff
    style G3 fill:#22c55e,color:#fff
    style E1 fill:#ef4444,color:#fff
    style E2 fill:#3b82f6,color:#fff
    style E3 fill:#22c55e,color:#fff
```
