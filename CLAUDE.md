# CLAUDE.md

## Project Overview
This project is an MVP for a **Saju-based matchmaking service**.

The goal is NOT just fortune telling.
The goal is to:
→ Match users based on compatibility (Saju)
→ Increase success probability with actionable recommendations

Core concept:
"Saju-based matching + probability boosting system"

---

## MVP Scope (Strict)
You MUST follow this scope strictly.

### In Scope
- Kakao login
- User birth data input (date, time, lunar/solar, gender)
- Basic saju calculation (can be placeholder)
- Compatibility score (0~100, rule-based)
- Match recommendation (2 users minimum)
- Free vs Paid feature separation
- Basic recommendation system:
  - color suggestion
  - place suggestion
  - styling suggestion

### Out of Scope
DO NOT implement:
- naming service
- physiognomy (face reading)
- feng shui advanced logic
- character system
- admin panel
- real payment integration
- real-time AI pipeline
- complex social features

---

## Tech Stack
- Backend: FastAPI
- Database: PostgreSQL (or Supabase)
- Auth: Kakao OAuth
- AI: optional (rule-based first, AI later)

---

## Core System Flow
1. User login
2. User inputs birth data
3. Saju calculation (basic)
4. Compatibility score calculation
5. Return 2+ match candidates
6. Show limited profile (blurred)
7. Paid → unlock details

---

## Coding Principles

### 1. Minimal Working First
Always implement the simplest working version.
Avoid over-engineering.

### 2. No Scope Expansion
Do NOT add features unless explicitly requested.

### 3. Use Placeholder When Needed
If domain logic is unclear:
- use simple rule-based logic
- add TODO for future replacement

### 4. Keep Code Simple
- small functions
- clear naming
- avoid unnecessary abstraction

---

## Domain Rules

### Compatibility Score
- Range: 0~100
- Start simple (rule-based)
- Accuracy is NOT critical for MVP

### Saju Logic
- Can be simplified
- Can return mock data if needed
- Real logic can be added later

---

## Output Rule (IMPORTANT)

When implementing tasks, ALWAYS respond in this format:

1. Understanding
2. In Scope
3. Out of Scope
4. Plan
5. Result

---

## Forbidden Actions

DO NOT:
- redesign entire architecture
- add unnecessary layers
- introduce complex patterns
- implement out-of-scope features
- assume unclear domain logic

---

## If Unclear
If something is unclear:
→ ask OR use minimal placeholder logic
→ NEVER over-assume