# Research Report: Lab-in-the-Loop — Nguồn cho Use Case Specification chuẩn

Ngày: 2026-07-16. Nguồn: `docs/use-case-lab-in-the-loop.md` (vision gốc từ meeting notes GSK), đối chiếu
`docs/system-architecture.md`, `docs/development-roadmap.md`, `docs/experiment-workflow.md`,
`docs/code-standards.md`, `docs/setup-and-operations.md`, và code `apps/canvus-mcp`,
`apps/lab-agent` (đọc trực tiếp `experiments.py`, `orchestrator.py`, `states.py`, `factory.py`,
`tool_bridge.py`) để xác nhận hành vi MVP thật. Không có web research (đúng constraint).

## 0. Quy ước phân loại trạng thái

- **[Vision]** — chỉ có trong meeting notes gốc, chưa/không có trong repo.
- **[MVP hiện tại]** — có code chạy được, xác nhận bằng file/module cụ thể.
- **[Future/Proposed]** — có trong roadmap/system-architecture như hướng đề xuất, **chưa được owner ratify** (system-architecture.md dòng 19, roadmap.md dòng 5).

---

## 1. Phạm vi hệ thống & Actors

Nguồn: use-case §2 (Người dùng chính), §9 (backend cần có), system-architecture §Component map.

| Actor | Vai trò | Trạng thái | Bằng chứng |
|---|---|---|---|
| Research scientist / Scientist | Đặt câu hỏi, chọn knowledge scope, khởi tạo `{idea: ...}` | **[MVP]** người dùng viết note `{idea:...}` trên canvas | UC §2.1, experiment-workflow.md "Example happy path" bước 3 |
| Lab scientist / technician | Review experiment design, chạy lab thật hoặc mock | **[MVP mock]** kết nối `Setup → Robot_`; **[Future]** review gate thật | UC §2.2, §12 Gate 1/3 |
| Data/imaging scientist | Chuẩn bị pipeline phân tích R/Python, đọc kết quả | **[Vision]**, chưa có code path | UC §2.3 |
| Engineer/platform team | Xây canvas workflow, connector, model orchestration | **[MVP]** = tác giả `canvus-mcp`/`lab-agent` | UC §2.4 |
| PI / Lab lead / project lead | Approve wet lab, resource | **[Vision/Future]** không có gate code | UC §2.5, §12 Gate 3 |
| AI harness/orchestrator | `lab-agent` — điều phối loop, gọi model, ghi canvas | **[MVP]** | system-architecture "Runtime apps" |
| Model provider (Claude/OpenAI/Ollama/vLLM/internal) | Sinh setup/result/decision | **[MVP một phần]** chỉ 2 adapter đặt tên `openai`/`claude`; Ollama/vLLM qua `LAB_AGENT_OPENAI_BASE_URL` dưới provider `openai`, không có adapter riêng | `lab_agent/adapters/factory.py:14-32` (đã đọc trực tiếp) |
| Canvus (canvas server) | Durable state store, nguồn sự thật của workflow | **[MVP]** | system-architecture §Overview, §State and idempotency |
| Internal knowledge sources (wiki/KG/vector DB/acronym dict) | Grounding context | **[Future]**; hiện chỉ có `RagCluster` connector-graph | system-architecture §Target harness boundary, `canvus_mcp/ragcluster.py` |
| In-silico / digital-twin service | Validate trước wet lab | **[Vision/Future]**, chưa triển khai | UC §8, roadmap Phase 5 |
| Robotic/wet-lab system | Thực thi thí nghiệm thật | **[Future]**; hiện `Robot_` là widget mock | roadmap "Real robot integration: Future" |
| Flywheel/imaging analysis | Auto-run gear phân tích (vd. lung fibrosis quantification) | **[Vision/Future]**, chưa có wrapper | UC §9.4, roadmap Phase 6 |
| Administrator/auditor | Audit log, observability, multi-user | **[Future]** | roadmap Phase 7; không có bằng chứng actor này trong UC §2 gốc — suy ra từ §15 "Audit Log" + Phase 7 |

**Ghi chú:** UC gốc không liệt kê "administrator/auditor" như actor con người tường minh; đây là actor suy luận từ yêu cầu Audit Log (§15) và Phase 7 (multi-user/observability). Nên đánh dấu là suy luận, không phải trích dẫn trực tiếp.

---

## 2. Quyết định phạm vi: giữ 3 UC gốc, không nổ ra micro-UC

Tài liệu vision đã tự đặt tên 3 UC ổn định — giữ nguyên, không chẻ nhỏ thêm:

- **UC-LITL-02 — Close the loop from design to new data** (top-level, §5): bao trùm toàn bộ vòng kín 9 bước.
- **UC-LITL-01 — Design next experiment from internal knowledge** (§4): supporting UC, là bước con/entry-point của UC-LITL-02 (chỉ tới main flow bước "Human scientist review").
- **UC-LITL-03 — In silico before wet lab** (§8): supporting UC, chèn giữa "Experiment design" và "Lab execution" của UC-LITL-02 khi có in-silico gate.

Không tạo thêm UC cho mỗi node canvas (10 node types ở §7 không phải 10 use case — chúng là artifact/state trong một use case).

---

## 3. UC-LITL-02 — Close the loop from design to new data (top-level)

**Goal:** Vòng lặp khép kín — từ internal knowledge, AI đề xuất experiment, experiment được thực thi (silico/human/robot), data mới được phân tích, knowledge được cập nhật có version, và round tiếp theo được đề xuất.

**Actors:** Primary = Scientist/Researcher. Supporting = Lab scientist, Data/imaging scientist, Lab lead, AI harness, Model provider, Canvus, Flywheel, In-silico service, Robotic/wet-lab.

**Trigger:** User tạo/connect `{idea: ...}` từ một `RAGCluster_` scope trên canvas [MVP], hoặc user hỏi trực tiếp câu hỏi khoa học [Vision, chưa có chat surface riêng ngoài canvas note].

**Preconditions:**
- Có knowledge scope đã nạp tài liệu [MVP: RagCluster connector graph; Future: wiki/KG/vector DB — UC §9.1].
- Model adapter khả dụng theo `LAB_AGENT_MODEL_PROVIDER` [MVP].

**Postconditions (happy path):** canvas có chuỗi node `idea → setup → robot/result → (loop back) → setup vNNN+1` hoặc `→ closed`; knowledge base version mới [Future, §9.5 chưa triển khai — hiện không có versioning service ngoài các note trên canvas].

### Main success flow (đối chiếu UC §5 9 bước với hiện trạng)

| # | Bước (vision) | Trạng thái | Bằng chứng |
|---|---|---|---|
| 1 | Knowledge grounding (wiki + historical data) | **[MVP một phần]** chỉ RagCluster context, không có wiki/KG/vector DB | system-architecture "Current limitations" |
| 2 | Experiment design — AI sinh proposal | **[MVP]** `lab-agent` tạo `[EXP:Setup vNNN]` | experiment-workflow.md "1. Knowledge to idea" |
| 3 | Review/validation (human hoặc in-silico) | **[Future]** không có approval gate trong code | system-architecture "Target harness boundary": "no human-approval gate in code" |
| 4 | Lab execution (human/robotic) | **[MVP mock]** widget `Robot_`, không nối robot/lab thật | UC §7 bảng ánh xạ |
| 5 | Data generation | **[MVP mock]** model emit `ExperimentResult` giả lập, gắn nhãn mock | experiment-workflow.md "Setup to mock robot result" |
| 6 | Automated analysis (Flywheel/HPC) | **[Future]** chưa có Flywheel wrapper | UC §9.4, roadmap Phase 6 |
| 7 | Result interpretation | **[MVP dạng mock]** `[EXP:Result vNNN]` do model sinh, rõ ràng là mock | UC §7 bảng |
| 8 | Knowledge update (versioned) | **[Future]** không có knowledge/versioning service | UC §9.5, system-architecture "Current limitations" |
| 9 | Redesign (next round) | **[MVP]** khi `LoopDecision=CONTINUE` → `[EXP:Setup vNNN+1]` | experiment-workflow.md "Result to next setup or close" |

### Alternate flows
- **STOP decision:** model trả `LoopDecision=STOP` → tạo `[EXP:Closed]` với reason/confidence/next-step, connect result→closed [MVP, `lab_agent/orchestrator.py`].
- **Manual re-trigger:** user connect lại result→setup thủ công để ép một round mới [MVP, ghi trong roadmap Phase 3 "Manual canvas edits can intentionally trigger a new round"].

### Exception flows
Đối chiếu UC §13 với code thật:

| Exception | Vision yêu cầu (§13) | Hiện trạng |
|---|---|---|
| Experiment design không đủ căn cứ (13.1) | Trả lý do + suggest in-silico/retrieve thêm | **[Future]** không thấy code path riêng biệt; model có thể hạ confidence nhưng không có response schema "cannot recommend" tường minh |
| Acronym ambiguous (13.2, vd `BIA`) | Detect → retrieve dict → hỏi confirm → regenerate | **[Future]** chỉ có nguyên tắc "không đoán acronym, hạ confidence" (`code-standards.md` "Grounding and scientific caution"), chưa có acronym dictionary/detect flow |
| Flywheel job fail (13.3) | Show failed, giữ data path, cho rerun, không update KB | **[Future]** — Flywheel chưa tồn tại |
| Data mâu thuẫn knowledge cũ (13.4) | Tạo conflict note, giữ cả 2 hypothesis | **[Future]** không có knowledge store để conflict |
| Loop vô hạn (13.5) | max iteration, stop condition, cost threshold | **[MVP một phần]** chỉ có `LAB_AGENT_LOOP_MAX_ROUNDS` backstop; chưa có cost threshold/duplicate detection | `setup-and-operations.md` "Runtime bounds" |
| MCP server unavailable | — | **[MVP]** CLI fail/log warning | experiment-workflow.md "Failure handling" |
| Model không emit đúng schema | — | **[MVP]** run hiện tại fail, canvas giữ pending, retry ở cycle sau | code-standards.md "Structured output" |

### Business rules
- Model chỉ được đọc (`READ_TOOLS`), orchestrator mới được ghi (`create_note`/`create_connector`) — tách biệt tuyệt đối [MVP, `lab_agent/tool_bridge.py`, `lab_agent/nodes.py`].
- Idempotency: idea chỉ xử lý nếu chưa có setup; setup chỉ chạy nếu chưa có result; loop connector chỉ xử lý 1 lần/phiên watcher [MVP, session-local `processed_loops`, không persistent qua restart — gap đã ghi trong experiment-workflow.md].
- Không được tự suy luận acronym/domain term nếu chưa retrieve context nội bộ [Vision §9.1, thực thi một phần bằng caution rule trong code-standards.md].
- Mock result phải luôn được gắn nhãn rõ ràng là mock [MVP].

### Data inputs/outputs
Input: RagCluster context, idea note text, setup note text, result note text.
Output: `[EXP:Setup vNNN]` (hypothesis/rationale/inputs/conditions/protocol/params/expected readouts/risks/success criteria), `[EXP:Result vNNN]` (mock summary/observations/metrics/QC flags/interpretation/caveats), `[EXP:Closed]` (decision/reason/confidence/learning/next step) — schema thật: `lab_agent/models/experiment.py` (`ExperimentSetup`, `ExperimentResult`, `LoopDecision`).

### Approval gates hiện trạng
UC §12 định nghĩa 5 gate (Gate 1–5) + 9 decision states (`DRAFT ... CLOSED/REJECTED`). Code có `DecisionState` enum đủ 9 giá trị (`lab_agent/models/states.py`) nhưng **chỉ 3 giá trị thực sự được ghi vào note**: `RUNNING` (khi tạo setup), `ANALYSIS_COMPLETE` (khi tạo result), `CLOSED` (khi stop) — xác nhận bằng grep `orchestrator.py:65,94,145`. Các state `DRAFT`, `NEEDS_REVIEW`, `APPROVED_FOR_IN_SILICO`, `APPROVED_FOR_WET_LAB`, `KNOWLEDGE_UPDATE_PENDING`, `REJECTED` **tồn tại trong enum nhưng không có code path nào gán chúng** — nghĩa là khung state machine đã phác thảo nhưng gate logic (Gate 1–5) hoàn toàn chưa cắm vào runtime. Đây là bằng chứng cụ thể nhất cho "no human-approval gate in code" mà system-architecture.md đã nêu ở mức tổng quát.

### Audit/idempotency needs
- **[MVP]**: idempotency theo connector-presence (xem trên).
- **[Future]**: audit log, retry/resume durable, per-canvas/per-user isolation — roadmap Phase 3 & 7.

### Current implementation status
MVP: happy path 9 bước rút gọn thành 3 marker chuỗi (`idea → setup → robot/result → loop`), chạy qua `canvus-mcp` (đọc/ghi) + `lab-agent` (điều phối) — không có silico/Flywheel/knowledge-versioning/approval gate thật.

### Acceptance criteria (map trực tiếp UC §14, MVP 1+2 — MVP hiện tại đã đạt phần lõi của MVP 1)
1. User note `{idea:...}` → connect RagCluster → agent tạo `[EXP:Setup v001]` [MVP đã có].
2. Connect setup→`Robot_` → agent tạo `[EXP:Result v001]` (rõ mock) [MVP đã có].
3. Connect result→setup → agent quyết định CONTINUE (`[EXP:Setup v002]`) hoặc STOP (`[EXP:Closed]`) [MVP đã có].
4. Không tạo trùng setup/result khi scan lặp lại [MVP đã có, idempotency rule].
5. MVP 2 (Flywheel-connected demo) và MVP 3 (in-silico gate) — **[Future]**, chưa có bằng chứng triển khai nào trong code hiện tại.

---

## 4. UC-LITL-01 — Design next experiment from internal knowledge (supporting)

**Goal:** Từ một knowledge scope + câu hỏi, tạo một experiment proposal duy nhất (không chạy tiếp phần thực thi).

**Actors:** Primary = Scientist. Supporting = AI harness, Model provider, Canvus.

**Trigger:** User chọn knowledge scope trên canvas và hỏi "Design the next experiment." [Vision UC §4]; hiện thực hoá MVP bằng note `{idea: ...}` connect từ `RAGCluster_` [MVP].

**Preconditions:** Knowledge scope đã có (RagCluster + feeders) [MVP]; constraints (budget/assay/disease area/equipment) — **[Vision]** optional input, chưa thấy field constraints tương ứng trong `ExperimentSetup` schema hiện tại (cần xác nhận thêm — xem Unresolved).

**Postconditions:** `[EXP:Setup vNNN]` được tạo và gắn connector từ idea [MVP].

**Main flow:** chọn scope → hỏi → retrieve → model tạo proposal → gắn node → human review → nếu approve, chuyển sang execution (= entry point vào UC-LITL-02 bước 3-4) [UC §4 "Main flow"].

**Alternate/Exception flows:** không tìm thấy context đủ mạnh → **[Future]** case 13.1 (chưa implement trả lời tường minh "insufficient internal evidence").

**Business rules:** proposal phải chứa đủ 10 mục theo §4 Output (hypothesis...recommended analysis pipeline) — đối chiếu với `ExperimentSetup` model thật để xác nhận field-parity (không đọc field-by-field trong scope report này; nên là việc của planner/implementer khi viết spec chi tiết).

**Data I/O:** Input = knowledge scope id, query text, optional constraints. Output = structured proposal 10 mục (§4).

**Approval gate:** Gate 1 (§12) — "Scientist approves experiment design" — **[Future]** chưa có nút approve/reject trong code (UC §7 bảng: "Human Review Node — Future — chưa có gate/nút approve").

**Current implementation status:** Bước tạo proposal = MVP (đã có). Bước "review → approve → chuyển execution" như một gate tường minh = Future.

**Acceptance criteria:** User hỏi → nhận `[EXP:Setup vNNN]` có đủ cấu trúc hypothesis/rationale/protocol/risks/success criteria [MVP đạt]; có nút/track approve rõ ràng trước khi sang execution [Future, chưa đạt].

---

## 5. UC-LITL-03 — In silico before wet lab (supporting, safety gate)

**Goal:** Chặn wet lab tốn kém bằng một bước validate mô phỏng trước khi cho phép chạy thật.

**Actors:** Primary = AI harness/in-silico service. Supporting = Scientist (review), Lab lead (sau đó).

**Trigger:** Một `[EXP:Setup]` đã được tạo và cần route quyết định trước khi đi wet lab [UC §8].

**Preconditions:** Setup đã tồn tại và (theo thứ tự chuẩn hoá) đã qua scientist review ban đầu.

**Postconditions:** Output JSON `{predicted_outcome, confidence, key_assumptions, risk_flags, recommended_changes, decision}` với `decision ∈ {proceed, revise_before_wet_lab, reject}` [UC §8 output schema].

**Main flow:** AI design → digital twin/in-silico sim → predicted outcome + confidence → recommendation (proceed/revise/reject) → human review → wet lab chỉ khi approved [UC §8, roadmap Phase 5 giữ nguyên workflow target].

**Business rules:** Không được gửi thẳng mọi experiment sang wet lab (đề xuất của Professor Do, được Minh đồng thuận mạnh để đưa vào SOW) [UC §8].

**Trạng thái hiện tại: [Vision/Future toàn bộ]** — không một dòng code nào trong `apps/canvus-mcp`/`apps/lab-agent` triển khai in-silico; roadmap Phase 5 xác nhận "Status: Future".

**Acceptance criteria (MVP 3, UC §14):** simulation trả predicted outcome + uncertainty; system recommend proceed/revise/reject; chỉ proceed sang lab nếu human approve. — Chưa đạt tiêu chí nào trong code hiện tại.

---

## 6. Reconcile thứ tự an toàn (safety order)

Chuẩn hoá xuyên suốt repo (README, system-architecture, roadmap, use-case §8 note đối chiếu):

```
AI design → in-silico validation → scientist review → lab lead approval → wet lab
```

Use-case gốc (§8, §12) diễn đạt hơi khác: "human approval" gộp chung, và Gate 1 (scientist approves design) xảy ra **trước** in-silico chứ không phải sau. Use-case §8 (dòng 348-354) đã tự giải thích: hai cách diễn đạt không mâu thuẫn nếu hiểu "scientist review" trong chuỗi chuẩn hoá bao gồm cả bước duyệt ban đầu (Gate 1) lẫn bước đọc kết quả in-silico trước khi lab lead duyệt tài nguyên (Gate 3). Roadmap Phase 6 dùng đúng chuỗi chuẩn hoá làm "canonical ordering" của repo này. **Kết luận cho spec canonical: dùng chuỗi 5 bước chuẩn hoá làm nguồn sự thật, chú thích rằng Gate 1 (design review) nằm trước in-silico, còn "scientist review" trong chuỗi 5 bước đại diện cho bước đọc-kết-quả-in-silico (Gate 2 output) trước khi lab lead duyệt (Gate 3).**

---

## 7. Workflow states & transitions

Hai lớp state khác nhau, không nên gộp khi viết spec:

1. **Marker/node state trên canvas (MVP thật):** `idea (no setup) → [EXP:Setup vNNN] (RUNNING) → [EXP:Result vNNN] (ANALYSIS_COMPLETE) → { [EXP:Setup vNNN+1] (RUNNING) | [EXP:Closed] (CLOSED) }`. Transition này do connector presence quyết định, không do một state machine tường minh lưu trữ (system-architecture "State and idempotency": "canvas is the durable source of workflow state; agent treats connector presence as the state transition signal").
2. **Decision-state lifecycle mục tiêu (UC §12, `DecisionState` enum đã khai báo đủ nhưng phần lớn chưa dùng):** `DRAFT → NEEDS_REVIEW → APPROVED_FOR_IN_SILICO → APPROVED_FOR_WET_LAB → RUNNING → ANALYSIS_COMPLETE → KNOWLEDGE_UPDATE_PENDING → CLOSED | REJECTED`. Hiện tại code chỉ chạm 3/9 state (`RUNNING`, `ANALYSIS_COMPLETE`, `CLOSED`) — 6 state còn lại là khung cho gate/approval **[Future]**, đã có type nhưng chưa có transition logic.

Spec canonical nên trình bày cả 2 lớp riêng biệt và map rõ: lớp 1 = hiện thực thật hôm nay; lớp 2 = state machine mục tiêu mà lớp 1 mới phủ một phần nhỏ.

---

## 8. Non-functional requirements

| NFR | Yêu cầu (nguồn) | Trạng thái |
|---|---|---|
| Security | Canvus credentials chỉ ở `.env` git-ignored; model chỉ nhận read tools; downloaded bytes không vào model context mặc định | **[MVP]** system-architecture "Trust boundaries" |
| Data locality | Một số dữ liệu không được gửi ra external provider; cần data-locality control theo provider/endpoint | **[Future]** roadmap Phase 4b; UC §10 "một số dữ liệu không được gửi ra external provider" |
| Model independence | Không phụ thuộc Claude-only; adapter interface theo provider | **[MVP một phần]** chỉ 2 adapter đặt tên (`openai`,`claude`); Ollama/vLLM/internal = **[Future]**, hiện chỉ chạy gián tiếp qua OpenAI-compatible `base_url` | UC §10, `factory.py`, `setup-and-operations.md` "Provider-neutral operation" |
| Traceability | Mỗi setup phải cite nguồn nội bộ đã dùng | **[Future]** roadmap Phase 4 "Every setup can cite the internal notes/PDFs/widgets it used" — chưa đạt |
| Reproducibility/versioning | Knowledge version không overwrite, tạo version mới có metadata | **[Future]** UC §9.5, không có Versioning Service |
| Reliability | Retry/resume, durable idempotency qua restart | **[Future]** roadmap Phase 3; hiện chỉ session-local `processed_loops` |
| Performance/scalability | Chunking/caching/resumable jobs cho multimodal lớn (case thực tế ~4 ngày ingest) | **[Future]** roadmap Phase 4c; UC §11 |
| Observability | Structured logs, metrics, health checks, multi-user dashboards | **[Future]** roadmap Phase 7; hiện chỉ log cảnh báo cơ bản khi lỗi transient |
| Cost/token governance | Budget/cost threshold, model routing theo task | **[Future]** roadmap Phase 4b; hiện chỉ có `LAB_AGENT_LOOP_MAX_ROUNDS` làm backstop số vòng, không phải cost/token |
| Accessibility/operability | `once`/`watch` CLI, `.env` config rõ ràng, troubleshooting doc | **[MVP]** setup-and-operations.md toàn bộ |

---

## 9. Traceability (source heading → current markers/tools/module)

| Vision heading | Current implementation marker |
|---|---|
| §4 UC-LITL-01 | `{idea: ...}` note, `RAGCluster_` widget, `scan_experiment_workflow.ideas_needing_setup` (`canvus_mcp/tools/experiments.py`) |
| §5 UC-LITL-02 | `lab_agent/orchestrator.py`, `lab_agent/watch.py`, `lab_agent/loop.py` |
| §7 Node types | Bảng ánh xạ đầy đủ đã có sẵn tại chính use-case doc dòng 278-292 — không cần suy diễn lại |
| §8 UC-LITL-03 in-silico | Không có module — 100% Future |
| §9.1 Knowledge Retrieval | `canvus_mcp/ragcluster.py` (chỉ RagCluster graph, không phải wiki/KG/vector DB thật) |
| §9.4 Flywheel | Không có — chỉ có tích hợp khác `integrations/canvus-serving-experiment-prepare/` (marker `{exp: ...}`, KHÔNG liên quan Flywheel, dùng cho action `experiment_prepare` riêng biệt của `canvus-serving`) |
| §10 Model-agnostic | `lab_agent/adapters/factory.py`, `openai_adapter.py`, `claude_adapter.py` |
| §12 Gates/decision states | `lab_agent/models/states.py` (enum khai báo đủ, 6/9 state chưa có transition) |
| §13 Error cases | `experiment-workflow.md "Failure handling"` (khớp một phần: MCP down, model schema fail, ambiguous input caveat, loop backstop; KHÔNG khớp: acronym-dictionary flow, Flywheel-fail flow, conflict-note flow) |

---

## 10. Câu hỏi chưa giải quyết (không tự suy đoán thêm)

1. `ExperimentSetup` schema thật (`lab_agent/models/experiment.py`) có field tương ứng đủ 10 mục ở UC §4 Output không (vd. `constraints.budget_limit`, `available_platforms`)? Chưa đọc field-by-field trong report này — cần planner/implementer đối chiếu khi viết spec chi tiết field-level.
2. "Administrator/auditor" actor — suy luận từ §15 Audit Log + roadmap Phase 7, không có tên actor tường minh trong UC gốc. Cần owner xác nhận đây có phải actor thật hay chỉ là hệ quả kỹ thuật (audit log) không cần actor con người riêng.
3. Target-harness direction (system-architecture "Target harness boundary", roadmap Phase 3-7) là **proposed, chưa ratify bởi project owner** — spec canonical cần quyết định: viết use case theo MVP-hiện-tại làm sự thật duy nhất, hay đồng thời mô tả target-harness như "future scope" chính thức? Đây là quyết định phạm vi tài liệu, không phải câu hỏi kỹ thuật.
4. Thứ tự "safety order" 5 bước và Gate 1-5 (UC §12) — đã note đối chiếu tự-giải-thích trong §8 nhưng không có xác nhận owner rằng cách hiểu "gộp scientist review" là đúng ý định gốc; nên hỏi lại nếu owner còn tham gia review.
