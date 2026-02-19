# Triggering the agent from an Azure DevOps work item

The agent exposes an HTTP API; Azure DevOps does not call it directly. You connect them with a **service hook** and a **Power Automate flow** (or Logic App) that forwards work item comment events to the agent.

**Mention handle:** The trigger is a **keyword in the comment text** (e.g. `@domeinteam_devops_agent`). You do *not* need an actual Azure DevOps user with that name — the flow simply checks whether the comment contains that string. Use a handle that fits your team (e.g. `@domeinteam_devops_agent`); the same string is used in the flow condition below.

---

## Flow in short

1. Someone adds a comment on a work item that contains your chosen handle (e.g. **`@domeinteam_devops_agent`**): *"@domeinteam_devops_agent investigate the auth bug"*.
2. Azure DevOps fires a **service hook** (webhook) when the work item is commented on.
3. The webhook calls a **Power Automate** flow (HTTP trigger).
4. The flow parses the payload, checks for your handle (e.g. `@domeinteam_devops_agent`), and sends **POST** to your Container App:  
   `https://<your-app>.azurecontainerapps.io/api/investigate`.

---

## 1. Create the Power Automate flow

### 1.1 HTTP trigger (receives Azure DevOps)

1. In [Power Automate](https://make.powerautomate.com), create a new **Instant cloud flow**.
2. Trigger: **When a HTTP request is received**.
3. Leave the flow open; you will paste the **HTTP POST URL** from this trigger into the service hook in step 2.

### 1.2 Request body schema (optional but useful)

In the trigger, under **Use sample payload to generate schema**, paste this so the flow has typed fields:

```json
{
  "eventType": "workitem.commented",
  "resource": {
    "id": 12345,
    "workItemId": 12345,
    "fields": {
      "System.WorkItemType": { "newValue": "Bug" },
      "System.Title": { "newValue": "Login fails" }
    },
    "comment": {
      "text": "@domeinteam_devops_agent investigate the auth bug"
    }
  },
  "resourceContainers": {
    "Project": { "id": "project-guid" }
  }
}
```

Azure DevOps may send slightly different shapes depending on the event; the important parts are the work item id and the comment text (see below).

### 1.3 Parse and filter

- Add a **Condition**:  
  - Check that the comment text contains your chosen handle, e.g. `@domeinteam_devops_agent`:  
    `contains(triggerBody()?['resource']?['comment']?['text'], '@domeinteam_devops_agent')` (or the equivalent path to the comment in your payload).
- **If yes**: continue to the HTTP action. **If no**: do nothing (optional: send a short response).

### 1.4 Call the agent API

- Add action **HTTP** (or **Invoke an HTTP request**).
  - **Method**: `POST`
  - **URI**: `https://<your-container-app-fqdn>/api/investigate`  
    Example: `https://devops-agent-app.bluegrass-12345.azurecontainerapps.io/api/investigate`
  - **Headers**:  
    `Content-Type` = `application/json`
  - **Body** (raw JSON):

```json
{
  "work_item_id": <work item id from trigger body>,
  "request_type": "investigation",
  "context": "<comment text from trigger body>",
  "report_only": true
}
```

In Power Automate you’ll replace the placeholders with dynamic content from the trigger, for example:

- **work_item_id**: `triggerBody()?['resource']?['workItemId']` or `triggerBody()?['resource']?['id']` (use whichever your DevOps payload uses).
- **context**: `triggerBody()?['resource']?['comment']?['text']` (or the field that holds the comment in your payload).

### 1.5 Respond to the service hook

- Add a **Response** action so Azure DevOps gets a 200 OK:
  - **Status code**: `200`
  - **Body**: e.g. `{ "message": "Agent run requested" }`

Save the flow, then copy the **HTTP POST URL** from the “When a HTTP request is received” trigger. You’ll use it in the next step.

---

## 2. Create the service hook in Azure DevOps

### Scope: one project = all teams and sprint boards

- A service hook subscription is created **per project**. Within that project, it applies to **all teams and all sprint boards** — any work item in the project that receives a comment will trigger the webhook. You don’t configure anything per team or per board.
- To cover **all projects in your org**: create **one subscription per project**, each with the **same** Power Automate HTTP URL. One flow then serves the whole org; every project’s comments go to the same flow, which calls the same agent.

### Filtering by team or sprint board

You can limit the agent to a specific **team** and/or **sprint** using the subscription **Filters** in the service hook wizard:

- **Area path** — Restricts to work items under the chosen area(s). In Azure DevOps, each **team** is tied to an area path (e.g. `ProjectName\TeamAlpha`). Select that area so only work items belonging to that team trigger the flow. You can select multiple areas (e.g. two teams) or a parent area to include all child teams.
- **Iteration path** — If the trigger’s filter list shows **Iteration path**, use it to restrict to a specific **sprint** (e.g. `ProjectName\Sprint 42`). Only work items in that iteration will trigger the webhook. Filter availability depends on your Azure DevOps version and the “Work item commented” trigger.

To find your team’s area path or a sprint’s iteration path: **Project settings** → **Project configuration** → **Areas** or **Iterations** (or **Team configuration** for a given team). Use those same paths in the service hook filters.

### Steps

1. In Azure DevOps, open the **project** you want to enable (repeat for each project if you want org-wide coverage).
2. Go to **Project settings** (bottom left) → **Service hooks**.
3. Click **+ Create subscription**.
4. **Trigger**:
   - **Select the trigger**: choose **Work item commented** (or **Work item updated** if you prefer to run on any update; then in the flow you can still filter by “comment added” and your handle, e.g. `@domeinteam_devops_agent`).
5. **Action**:
   - **Select an action**: **Web hook**.
   - **URL**: paste the Power Automate HTTP POST URL from step 1 (same URL for every project if you want one agent for the whole org).
   - Leave **Authentication** as None unless you’ve added auth to the flow (e.g. Bearer token).
6. **Filters** (optional):
   - Leave empty to include **all** work items (all teams, all boards) in the project.
   - To restrict to a **specific team or sprint**: set **Area path** to the team’s area (e.g. `Project\YourTeam`), and **Iteration path** to the sprint if the trigger offers it (see “Filtering by team or sprint board” above).
7. Finish the wizard and **Create** the subscription.
8. **Other projects**: repeat steps 1–7 for each project; use the same flow URL so the same agent runs for the whole org.

After this, any comment on a work item in scope will send a POST to your flow; the flow only calls the agent when the comment contains your handle (e.g. `@domeinteam_devops_agent`).

---

## 3. Test it

1. Open any work item in that project.
2. Add a comment that contains your handle, e.g. `@domeinteam_devops_agent`:  
   *"@domeinteam_devops_agent investigate the auth bug"*
3. Save the comment.
4. In Power Automate, check **Run history** for the flow to see the run and any errors.
5. On the work item, after a short delay you should see a new comment from the agent with the investigation result.

---

## API reference (for the flow body)

The agent expects a JSON body like:

| Field           | Type    | Required | Description |
|----------------|---------|----------|-------------|
| `work_item_id` | integer | Yes      | Azure DevOps work item ID. |
| `request_type` | string  | No       | `investigation` (default), `feature_request`, or `bug`. |
| `context`      | string  | No       | Extra context (e.g. the comment text). |
| `report_only`  | boolean | No       | `true` (default) = only post a comment; `false` = allow branch/PR creation. |

Example:

```json
{
  "work_item_id": 12345,
  "request_type": "investigation",
  "context": "@domeinteam_devops_agent investigate the auth bug",
  "report_only": true
}
```

Response (e.g. 200):

```json
{
  "job_id": "abc123...",
  "status": "processing",
  "message": "Job started (direct mode)"
}
```

You can then poll `GET /api/status/{job_id}` if you want to show status in the flow; for “fire and forget” you can ignore the response after 200.

---

## Troubleshooting

- **Flow not triggering**  
  Confirm the service hook is for the correct project/area and that the event (e.g. “Work item commented”) matches when you add the comment.

- **Agent not running**  
  In Power Automate run history, check that the HTTP action returns 200 and that the request body matches the schema above (especially `work_item_id` as integer).

- **Wrong work item or comment**  
  Inspect the trigger body in a test run and map the correct nodes (e.g. `resource.workItemId`, `resource.comment.text`) to the API fields. DevOps payloads can vary by event type.

- **Container App not reachable**  
  Ensure the Container App ingress is enabled and the URL is the one from the Bicep output (`containerAppFqdn`). If the flow runs in a corporate network, ensure it can reach `*.azurecontainerapps.io`.
