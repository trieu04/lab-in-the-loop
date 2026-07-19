## Use case: **Lab-in-the-Loop**

> **Ghi chú trạng thái (2026-07-16):** Tài liệu này mô tả **tầm nhìn (vision)** đầy đủ của use case, lấy từ meeting notes gốc. Phần triển khai hiện tại trong repo này (`apps/canvus-mcp` + `apps/lab-agent`) chỉ hiện thực một tập con: vòng lặp mock robot dựa trên connector Canvus, chưa có Flywheel wrapper, in-silico simulation, wiki/knowledge-graph retrieval, hay adapter riêng cho Ollama/vLLM. Các phần bên dưới được chú thích **[MVP hiện tại]** hoặc **[Future/chưa triển khai]** khi cần phân biệt. Xem [System architecture](../system-architecture.md) và [Roadmap](../development-roadmap.md) để biết ranh giới MVP so với kiến trúc harness được đề xuất (proposed, chờ owner xác nhận).

**Lab-in-the-loop** là use case trung tâm của Phase 3 *(lưu ý: "Phase 3" ở đây là một giai đoạn chương trình/nghiệp vụ bên ngoài của GSK, mô tả trong meeting notes gốc — khác với "Phase 3" trong `docs/development-roadmap.md` của repo này, vốn là "Persistent loop state")*: hệ thống không chỉ đọc tài liệu và trả lời câu hỏi, mà dùng knowledge nội bộ để **đề xuất thí nghiệm**, đưa thí nghiệm đó vào **lab / robotic lab / Flywheel analysis**, sau đó lấy dữ liệu mới quay lại để **cập nhật knowledge base và thiết kế vòng thí nghiệm tiếp theo**. Trong transcript, Minh mô tả rất rõ rằng GSK không muốn chỉ “chit-chat” hay summarize tài liệu, mà muốn biến AI output thành hành động thật trong lab. 

---

# 1. Mục tiêu của use case

Mục tiêu là tạo một vòng lặp tự động hoặc bán tự động:

```text id="ha92eu"
Internal Knowledge / Wiki
→ AI Experiment Design
→ Human / In Silico / Robotic Lab Execution
→ New Data
→ Automated Analysis
→ Knowledge Update
→ Next Experiment Design
```

Nói đơn giản:

**AI đọc lịch sử nghiên cứu → đề xuất experiment → experiment được chạy → dữ liệu mới được phân tích → AI học từ dữ liệu mới → đề xuất experiment tốt hơn.**

Điểm khác biệt so với một hệ thống Q&A bình thường là output của AI không dừng ở text. Nó có thể trở thành một **actionable experiment plan**.

---

# 2. Người dùng chính

## Primary users

### 1. Scientist / Researcher

Người đặt câu hỏi khoa học, ví dụ:

```text id="ofxi5b"
Based on 10 years of GSK lung fibrosis research, what should be the next experiment?
```

Họ muốn hệ thống đề xuất thí nghiệm mới dựa trên knowledge nội bộ.

### 2. Lab scientist

Người review experiment design, kiểm tra xem có hợp lý không, có chạy được trong lab không.

### 3. Data scientist / imaging scientist

Người chuẩn bị analysis code, ví dụ R/Python script để xử lý micro-CT, omics, imaging hoặc các dữ liệu thí nghiệm khác.

### 4. Engineer / platform team

Người xây workflow trên canvas, connector, model orchestration, Flywheel integration, versioning và knowledge update.

### 5. PI / project lead

Người quyết định hướng nghiên cứu, approve việc chạy wet lab hoặc robotic lab.

---

# 3. Use case ở mức business

## Business problem

Trong drug discovery, knowledge cũ rất nhiều nhưng phân tán:

* paper
* PowerPoint
* internal report
* imaging data
* omics data
* experiment history
* human tacit knowledge
* code phân tích dữ liệu
* cloud/HPC storage
* robotic lab systems

Nếu AI chỉ hỏi-đáp tài liệu thì chưa đủ giá trị. GSK cần một hệ thống giúp biến knowledge cũ thành **new experiment**, rồi biến data mới thành **next decision**.

## Business value

Lab-in-the-loop giúp:

1. Rút ngắn thời gian từ insight đến experiment.
2. Dùng lại 10 năm knowledge nội bộ thay vì bắt đầu lại từ đầu.
3. Tạo vòng lặp “compound interest” cho knowledge: mỗi experiment tạo data mới, data mới làm knowledge mạnh hơn.
4. Giảm phụ thuộc vào việc con người phải copy-paste tài liệu, hỏi AI, rồi tự nhập lại kết quả.
5. Tạo nền tảng cho robotic lab hoặc semi-autonomous lab workflow.

---

# 4. Use case ở mức product

## UC-LITL-01: Design next experiment from internal knowledge

### User story

Là một scientist, tôi muốn chọn một tập tài liệu / knowledge scope trên canvas và hỏi hệ thống thiết kế thí nghiệm tiếp theo, để tôi có một experiment proposal dựa trên knowledge nội bộ chứ không phải kiến thức internet chung chung.

### Input

* Internal wiki / knowledge graph.
* Historical documents.
* Previous experiment results.
* Canvas context.
* User question.
* Optional: constraints như budget, assay type, disease area, available equipment.

### Output

Experiment proposal gồm:

```text id="v5w5hv"
- Hypothesis
- Scientific rationale
- Experimental design
- Required samples / compounds / cell lines / animal model
- Protocol outline
- Expected readouts
- Success criteria
- Risk / uncertainty
- Data to collect
- Recommended analysis pipeline
```

### Main flow

```text id="3658vu"
1. User chọn knowledge scope trên canvas.
2. User hỏi: “Design the next experiment.”
3. System retrieve internal wiki + relevant documents.
4. Model tạo experiment proposal.
5. System gắn proposal vào canvas như một Experiment Design Node.
6. Human scientist review.
7. Nếu approve, proposal được chuyển sang bước execution.
```

---

# 5. Use case ở mức workflow đầy đủ

## UC-LITL-02: Close the loop from design to new data

Đây là use case đầy đủ nhất.

### Main happy path

```text id="wiw97w"
Step 1: Knowledge grounding
Internal wiki + historical data được retrieve theo context.

Step 2: Experiment design
AI sinh ra experiment proposal.

Step 3: Review / validation
Human hoặc in silico module kiểm tra proposal.

Step 4: Lab execution
Experiment được chạy bởi human lab hoặc robotic lab.

Step 5: Data generation
Experiment tạo ra raw data: imaging, omics, tables, assay output, etc.

Step 6: Automated analysis
Flywheel hoặc HPC pipeline chạy analysis code.

Step 7: Result interpretation
AI đọc analyzed results và so sánh với hypothesis ban đầu.

Step 8: Knowledge update
System cập nhật wiki / knowledge graph / version history.

Step 9: Redesign
AI đề xuất experiment vòng tiếp theo.
```

Minh mô tả trong meeting rằng sau khi AI design experiment, GSK có thể thật sự đem experiment đó vào lab, có thể là human lab hoặc robotic lab. Dữ liệu mới sau đó có path, có code phân tích R/Python, và có thể được đưa vào Flywheel để chạy analysis tự động. 

---

# 6. Concrete example: lung fibrosis micro-CT

Ví dụ cụ thể từ meeting là **lung fibrosis imaging study**.

## Scenario

GSK có nhiều năm dữ liệu và knowledge về lung fibrosis. User hỏi hệ thống:

```text id="kxbl0c"
Design the next experiment for lung fibrosis based on GSK internal knowledge.
```

System sinh experiment proposal. Experiment được chạy, tạo ra micro-CT scans. Khi scan được upload vào Flywheel, custom gear tự động chạy lung segmentation và fibrosis quantification. Trong transcript, pipeline micro-CT được mô tả là đã có custom gear, auto-trigger, version `0.1.0`, và validation 6/6 scans match ground truth. 

## Flow cụ thể

```text id="pve48l"
GSK internal wiki
→ AI proposes lung fibrosis experiment
→ lab collects micro-CT scans
→ scans uploaded to Flywheel
→ Flywheel auto-runs segmentation / quantification gear
→ analyzed fibrosis metrics produced
→ AI interprets result
→ wiki updates
→ AI recommends next lung fibrosis experiment
```

## Output sau analysis

Có thể gồm:

```text id="rsmv9j"
- Fibrosis score
- Lung volume
- Segmentation mask
- Treatment arm comparison
- Statistical summary
- Quality control flags
- Interpretation against original hypothesis
- Recommendation for next round
```

---

# 7. Canvas representation

Trên canvas, use case này nên được biểu diễn bằng các node và connector có hướng.

## Node types

```text id="fvcxck"
1. Knowledge Scope Node
2. Query Node
3. Experiment Design Node
4. Human Review Node
5. In Silico Simulation Node
6. Lab Execution Node
7. Flywheel Data Node
8. Analysis Gear Node
9. Result Interpretation Node
10. Knowledge Update Node
11. Next Experiment Node
```

## Example canvas graph

```text id="vs9g34"
[Internal Wiki / Knowledge Scope]
        ↓
[Query: Design next experiment]
        ↓
[Experiment Design]
        ↓
[In Silico Simulation]
        ↓
[Human Approval]
        ↓
[Lab / Robot Execution]
        ↓
[Flywheel Data Upload]
        ↓
[Analysis Gear]
        ↓
[Result Interpretation]
        ↓
[Knowledge Update]
        ↓
[Next Experiment Design]
```

Connector rất quan trọng vì nó biểu diễn dependency: node nào là input của node nào, data đi theo hướng nào, version nào được tạo ra sau mỗi vòng.

## Ánh xạ sang marker hiện tại trong repo [MVP hiện tại]

| Node khái niệm (mục 7) | Marker/thực thi hiện tại | Trạng thái |
|---|---|---|
| Knowledge Scope Node | Widget ảnh `RAGCluster_` | Đã có |
| Query Node | Note `{idea: ...}` | Đã có |
| Experiment Design Node | Note `[EXP:Setup vNNN]` | Đã có |
| Human Review Node | — | Future — chưa có gate/nút approve trong code |
| In Silico Simulation Node | — | Future — chưa triển khai (xem mục 8) |
| Lab Execution Node | Widget `Robot_` (mock) | MVP hiện tại chỉ mock, chưa nối robot/lab thật |
| Flywheel Data Node | — | Future — chưa có wrapper Flywheel |
| Analysis Gear Node | — | Future |
| Result Interpretation Node | Note `[EXP:Result vNNN]` (rõ ràng là mock) | Đã có (dạng mock) |
| Knowledge Update Node | — | Future — chưa có knowledge/versioning service |
| Next Experiment Node | `[EXP:Setup vNNN+1]` khi model quyết định CONTINUE | Đã có |

`[EXP:Closed]` (quyết định STOP + lý do) là marker bổ sung của repo này, không có tên tương ứng trực tiếp trong danh sách node khái niệm ở trên.

---

# 8. In silico extension

Một bổ sung rất quan trọng từ Professor Do là **không nên gửi thẳng mọi experiment sang wet lab**. Trước đó có thể chạy **in silico / digital twin** để kiểm tra thiết kế. Ý tưởng này được Minh đồng ý mạnh và muốn đưa vào SOW vì giúp tránh lãng phí khi wet lab có thể tốn nhiều ngày và rất nhiều tiền. 

## Use case phụ: UC-LITL-03 — In silico before wet lab

### Flow

```text id="hg49uu"
AI Experiment Design
→ Digital Twin / In Silico Simulation
→ Predicted outcome
→ Confidence / uncertainty
→ Recommendation: proceed, revise, or reject
→ Human review
→ Wet lab only if approved
```

### Output của in silico node

```text id="5sw63l"
{
  "predicted_outcome": "...",
  "confidence": 0.72,
  "key_assumptions": [],
  "risk_flags": [],
  "recommended_changes": [],
  "decision": "revise_before_wet_lab"
}
```

## Vì sao quan trọng?

Nếu lab-in-the-loop chạy sai design, hậu quả có thể là:

* tốn hóa chất
* tốn antibody
* tốn animal model
* tốn robot/lab time
* tạo ra data vô ích
* làm knowledge graph update sai

Vì vậy, version production nên có validation gate:

```text id="2dhwrt"
AI design
→ internal knowledge grounding
→ in silico validation
→ human approval
→ lab execution
```

**Ghi chú đối chiếu thứ tự approval:** Thứ tự chuẩn hoá được dùng xuyên suốt tài liệu của repo này (README, system architecture, roadmap) là:

```text
AI design → in-silico validation → scientist review → lab lead approval → wet lab
```

Thứ tự này tương thích với sơ đồ trên (gộp "human approval" thành hai bước cụ thể: scientist review rồi lab lead approval). Nó cũng không mâu thuẫn với các gate ở mục 12 bên dưới — Gate 1 (scientist approves design) diễn ra *trước* khi đưa vào in-silico, còn Gate 2 (in-silico threshold) và các gate approval khác diễn ra sau, khớp với thứ tự "AI design → in-silico → scientist review → lab lead approval → wet lab" nếu hiểu "scientist review" ở đây bao gồm cả bước duyệt ban đầu lẫn bước đọc kết quả in-silico trước khi lab lead duyệt tài nguyên.

---

# 9. Backend architecture cần có

*Toàn bộ mục 9 mô tả kiến trúc backend mục tiêu (target), phần lớn là* **[Future/chưa triển khai]***. Hiện tại chỉ có RagCluster connector-graph (`canvus_mcp/ragcluster.py`) làm nguồn context; không có knowledge retrieval service, execution orchestrator, Flywheel wrapper, hay knowledge update service riêng biệt.*

## 9.1. Knowledge Retrieval Service

Dùng để lấy context từ:

```text id="ihxt5b"
- internal wiki
- knowledge graph
- vector database
- acronym dictionary
- previous experiment history
- analysis result archive
```

Quan trọng: model không được tự suy luận acronym hoặc domain term nếu chưa retrieve context nội bộ. Transcript nhắc rõ nếu model hiểu sai acronym như BIA, toàn bộ graph và reasoning phía sau sẽ sai theo. 

---

## 9.2. Experiment Design Agent

Agent nhận:

```json id="1yocnz"
{
  "query": "Design next experiment for lung fibrosis",
  "knowledge_scope_id": "scope_001",
  "constraints": {
    "disease_area": "lung fibrosis",
    "available_platforms": ["micro-CT", "cell culture", "robotic lab"],
    "budget_limit": null,
    "requires_human_approval": true
  }
}
```

Agent trả về:

```json id="6yfuqb"
{
  "experiment_design_id": "exp_001",
  "hypothesis": "...",
  "rationale": "...",
  "protocol_outline": [],
  "required_inputs": [],
  "expected_outputs": [],
  "analysis_plan": [],
  "risk_flags": [],
  "confidence": 0.68
}
```

---

## 9.3. Execution Orchestrator

Orchestrator quyết định experiment đi về đâu:

```text id="cf8oti"
- In silico simulation
- Human lab
- Robotic lab
- Flywheel analysis only
- Hold for human review
```

Nó không nên tự động gửi wet lab nếu thiếu approval.

---

## 9.4. Flywheel Integration

Flywheel không cần engineer build lại từ đầu, vì trong meeting Minh nói đây là cloud/HPC/data platform đã có sẵn: nó có thể host data, chạy compute, chạy Docker image, auto-trigger và trả kết quả nhanh. 

Engineer cần làm wrapper để canvas gọi được Flywheel.

### Flywheel job input

```json id="2rjs98"
{
  "data_path": "flywheel://project/session/acquisition",
  "gear_name": "lung-fibrosis-quantification",
  "gear_version": "0.1.0",
  "parameters": {
    "segmentation_method": "elliot_mckinley_pipeline",
    "qc_enabled": true
  }
}
```

### Flywheel job output

```json id="uem8n4"
{
  "job_id": "fw_job_123",
  "status": "completed",
  "outputs": {
    "metrics_csv": "...",
    "segmentation_mask": "...",
    "summary_json": "...",
    "qc_report": "..."
  }
}
```

---

## 9.5. Knowledge Update Service

Sau khi result được phân tích, hệ thống phải cập nhật knowledge base.

Không nên overwrite version cũ. Cần tạo version mới:

```text id="rjgx1p"
knowledge_scope_001/
  versions/
    2026-07-07T10-00-00_initial/
    2026-07-10T15-20-00_after_exp_001/
    2026-07-15T12-30-00_after_exp_002/
```

Mỗi version nên lưu:

```json id="s3mxqa"
{
  "version_id": "v003",
  "source_experiment_id": "exp_001",
  "input_data_ids": [],
  "analysis_job_ids": [],
  "summary": "...",
  "new_findings": [],
  "changed_assumptions": [],
  "next_recommendations": [],
  "created_at": "..."
}
```

---

# 10. Model-agnostic requirement

**[MVP hiện tại]** `lab-agent` hiện chỉ có hai adapter đặt tên: `openai` và `claude` (`lab_agent/adapters/factory.py`). Ollama/vLLM/internal model **không có adapter riêng**; chúng chỉ chạy được gián tiếp qua `LAB_AGENT_OPENAI_BASE_URL` (endpoint tương thích OpenAI) dưới provider `openai`. Yêu cầu "adapter riêng cho mỗi provider" bên dưới vẫn là **[Future]**.

Một requirement quan trọng là Lab-in-the-loop **không được phụ thuộc Claude-only**. Trong meeting, Minh nói vấn đề hiện tại là Minh có thể chạy lab-in-the-loop vì có Claude license/setup, nhưng người khác có thể không chạy được. Vì vậy cần model swap: Claude, OpenAI, Ollama, vLLM hoặc internal model đều phải có khả năng chạy cùng workflow. 

## Requirement

```text id="53nnfj"
The Lab-in-the-Loop workflow must be model-provider agnostic.
```

## Model adapter interface

```json id="9vdtou"
{
  "model_provider": "openai | claude | ollama | vllm | internal",
  "task_type": "experiment_design | result_interpretation | knowledge_update",
  "context": {},
  "tools": [],
  "output_schema": {}
}
```

## Vì sao cần?

Vì khi scale cho nhiều team:

* không phải ai cũng có Claude license
* token budget khác nhau
* một số team cần local/internal model
* một số dữ liệu không được gửi ra external provider
* mỗi model có context length và multimodal ability khác nhau

---

# 11. Scalability requirement

Use case này phải xử lý được cả input đơn giản và input khoa học phức tạp.

## Case đơn giản

```text id="d64sip"
Text query:
"Design next experiment."
```

Case này có thể chạy nhanh.

## Case thực tế

```text id="hbhi1n"
PDF + PowerPoint + video + images + graphs + tables + omics + imaging data
```

Trong meeting, Shaw/Minh nói có ingestion case với video/images/complex graph mất nhiều ngày, thậm chí khoảng 4 ngày với model mạnh, nên scalability là một blocker thực sự. 

## Requirement

Hệ thống cần:

```text id="py96fa"
- chunking
- caching
- resumable jobs
- staged summarization
- modality-specific extraction
- cost/token budget
- progress tracking
- failure recovery
```

---

# 12. Human approval và safety gate

Lab-in-the-loop không nên fully autonomous ngay từ đầu.

## Minimum approval gates

```text id="bkgcxi"
Gate 1: Scientist approves experiment design
Gate 2: In silico result passes threshold
Gate 3: Lab lead approves resource usage
Gate 4: Data scientist approves analysis pipeline
Gate 5: Knowledge update is reviewed before becoming canonical
```

## Decision states

```text id="3zq5b5"
DRAFT
NEEDS_REVIEW
APPROVED_FOR_IN_SILICO
APPROVED_FOR_WET_LAB
RUNNING
ANALYSIS_COMPLETE
KNOWLEDGE_UPDATE_PENDING
CLOSED
REJECTED
```

---

# 13. Error cases cần xử lý

## 13.1. Experiment design không đủ căn cứ

System phải trả về:

```text id="iucpq6"
Cannot recommend wet lab execution yet.
Reason: insufficient internal evidence.
Suggested action: retrieve more prior studies or run in silico first.
```

## 13.2. Acronym bị ambiguous

Ví dụ `BIA`.

System không được tự đoán. Nó phải:

```text id="b4nki5"
1. Detect acronym.
2. Retrieve internal acronym dictionary.
3. Ask for confirmation nếu vẫn ambiguous.
4. Regenerate graph nếu user sửa.
```

## 13.3. Flywheel job fail

System phải:

```text id="dp0p28"
- show failed status on canvas
- keep original data path
- show logs
- allow rerun
- not update knowledge base with incomplete result
```

## 13.4. New data contradicts prior knowledge

System không nên xóa hypothesis cũ ngay. Nó nên tạo conflict note:

```text id="44y32z"
New result conflicts with previous assumption X.
Recommended: preserve both hypotheses and create branch for follow-up validation.
```

## 13.5. Loop chạy vô hạn

Cần có:

```text id="nrltny"
- max iteration
- stop condition
- human approval before next lab run
- duplicate detection
- cost threshold
```

---

# 14. Acceptance criteria cho MVP

## MVP 1: Text-only lab-in-the-loop simulation

Pass nếu hệ thống làm được:

```text id="ebd49s"
1. User hỏi design next experiment.
2. System retrieve internal wiki/context.
3. System tạo experiment design node.
4. User mock approve.
5. User upload/mock result.
6. System interpret result.
7. System update knowledge version.
8. System suggest next experiment.
```

---

## MVP 2: Flywheel-connected demo

Pass nếu hệ thống làm được:

```text id="2gntnz"
1. Canvas có Experiment Design Node.
2. Node này connect tới Flywheel Data Node.
3. Flywheel job chạy hoặc mock chạy.
4. Analysis output quay lại canvas.
5. Result Interpretation Node được tạo.
6. Knowledge Update Node được version hóa.
7. Next Experiment Node được sinh ra.
```

---

## MVP 3: In silico gate

Pass nếu hệ thống làm được:

```text id="f2kivk"
1. AI design experiment.
2. System chạy một digital twin / simulation wrapper đơn giản.
3. Simulation trả predicted outcome + uncertainty.
4. System recommend proceed / revise / reject.
5. Chỉ proceed sang lab nếu human approve.
```

---

# 15. Engineering deliverables

## Backend

```text id="bbj7og"
- Knowledge Retrieval API
- Model Adapter Layer
- Experiment Design Agent
- Execution Orchestrator
- Flywheel Wrapper
- Knowledge Update Service
- Versioning Service
- Audit Log
```

## Frontend / Canvas

```text id="1ibd9s"
- Lab workflow node types
- Directed connectors
- Run status on each node
- Human approval buttons
- Version history viewer
- Error / warning display
- Result summary card
```

## Data / ML

```text id="7i550x"
- Acronym dictionary
- Domain ontology
- Experiment schema
- Result interpretation schema
- In silico prototype
- Evaluation metrics
```

---

# 16. Một câu mô tả ngắn để đưa vào proposal

**Lab-in-the-loop is a closed-loop drug discovery workflow in which AI grounds experiment design in internal GSK knowledge, proposes actionable experiments, validates them through in silico simulation or human review, executes them through human/robotic lab and Flywheel analysis pipelines, and feeds the resulting data back into a versioned knowledge graph to generate the next round of experiment recommendations.**

Bản tiếng Việt:

**Lab-in-the-loop là một workflow vòng kín cho drug discovery, trong đó AI sử dụng knowledge nội bộ của GSK để thiết kế thí nghiệm, kiểm tra thiết kế bằng in silico hoặc human review, chuyển thí nghiệm sang human/robotic lab và Flywheel analysis, rồi đưa dữ liệu mới quay lại knowledge graph để tạo đề xuất thí nghiệm tiếp theo.**
