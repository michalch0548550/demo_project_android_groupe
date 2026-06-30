# תרשימי זרימה — AppsFlyer MCP Test Runner

> **איך לשתף:** העלה את הקובץ ל-GitHub, שלח את הקישור, או הדבק את קוד ה-Mermaid ב-[mermaid.live](https://mermaid.live) ולחץ Share.

---

## 1. זרימת הבדיקה הראשית (8 צמתים)

```mermaid
flowchart TD
    START([python main.py]) --> SETUP[1. setup_environment]

    SETUP -->|test_status != FAIL| AGENT[2. run_agent_prompt]
    SETUP -->|test_status == FAIL| FAIL[7. fail_node]

    AGENT -->|יש tool_calls| VERIFY[3. verify_mcp_activation]
    AGENT -->|אין tool_calls| FAIL

    VERIFY -->|mcp_triggered == true| APPLY[4. apply_sdk_changes]
    VERIFY -->|mcp_triggered == false| FAIL

    APPLY -->|files_modified == true| COMPILE[5. check_compilation]
    APPLY -->|files_modified == false| FAIL

    COMPILE -->|compilation_passed == true| PASS[6. pass_node]
    COMPILE -->|compilation_passed == false| FAIL

    PASS --> REPORT[8. end_report]
    FAIL --> REPORT
    REPORT --> END([סיום + mcp_test_report.json])
```

---

## 2. זרימת שאלות-תשובות (responseAgent — מוכן, לא מחובר)

```mermaid
flowchart TD
    AGENT[run_agent_prompt] --> CLASS[classify_agent_response]
    CLASS -->|SUCCESS| AGENT
    CLASS -->|FAIL| FAIL[fail_node]
    CLASS -->|QUESTION| ANSWER[answer_question_node]
    ANSWER -->|עד 4 סבבים| AGENT
    ANSWER -->|חריגה מ-MAX_QUESTION_ROUNDS| FAIL
```

---

## 3. מקור התשובה לשאלות (responseAgent)

```mermaid
flowchart TD
    Q[שאלה מהסוכן] --> CAT[classify_question — regex]
    CAT -->|platform / listener / onelink / dev_key| DET[תשובה דטרמיניסטית]
    CAT -->|general| LLM[Gemini + ANSWER_PROMPT + context]
    DET --> BUILD[build_prompt_with_answers]
    LLM --> BUILD
    BUILD --> AGENT[run_agent_prompt — ניסיון חוזר]
```

---

## קישורים לשיתוף מהיר

| פעולה | קישור |
|--------|--------|
| עורך Mermaid + שיתוף | https://mermaid.live |
| תיעוד Mermaid | https://mermaid.js.org |

### הוראות שיתוף ב-mermaid.live

1. פתח https://mermaid.live
2. מחק את הקוד בצד שמאל
3. הדבק אחד מבלוקי ה-`mermaid` למעלה (בלי הסימנים \`\`\`)
4. לחץ **Actions → Share** (או Export PNG/SVG)
