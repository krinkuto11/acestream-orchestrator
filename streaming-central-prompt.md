**Role:** You are an elite Observability Frontend Engineer and UX Architect. You specialize in building high-frequency Network Operations Centers (NOC), high-density data visualizations, and real-time streaming dashboards.

**Task:** Complete overhaul of the frontend for **AceStream Orchestrator**, transforming it into a professional-grade "Streaming Central." The system manages dynamic Docker containers (Engines), proxies live video streams (MPEG-TS/HLS), routes traffic through redundant Gluetun VPN tunnels, and tracks granular P2P and proxy metrics.

**Recommended Tech Stack & Tooling:**
* **Core:** React (Vite), TypeScript, Tailwind CSS (Dark-mode optimized, using Slate/Zinc with semantic severity colors).
* **State Management:** `zustand` (Crucial: Avoid React Context for high-frequency 1-second polling intervals to prevent re-render hell).
* **Base UI Shell:** **ShadCN UI** + Radix Primitives (Keep this for the layout shell, modals, sidebars, and data tables).
* **Advanced Visualization:** **Apache ECharts** (via `echarts-for-react`) OR **Visx** (by Airbnb). Standard Chart.js is not sufficient for the density required. We need high-performance heatmaps, scatter plots, and multi-axis synchronizations.
* **Network Topology:** **React Flow** (for mapping live routing logic and VPN failovers).

**MCP (Model Context Protocol) Directives:**
*(If your environment supports MCPs, execute these steps before scaffolding the UI)*:
1.  **File System MCP:** Inspect the Python backend schemas (e.g., `app/models/schemas.py` and `app/proxy/constants.py`) to automatically generate accurate Zod/TypeScript interfaces for the frontend state.
2.  **Docker/SQLite MCP (Optional):** If configured, briefly query the live or mocked SQLite database (`app/data/orchestrator.db`) or Docker daemon to understand the exact shape of the Engine and Stream data payloads.

**Core Directives for "Streaming Central":**
1.  **The "5-Second Rule" (NOC Video Wall):** The default view must allow an operator to identify network bottlenecks, tripped circuit breakers, or OOM-crashing engines from across the room.
2.  **Information Density over Whitespace:** Use compact layouts, mini-sparklines in table rows, and tight spatial groupings.

**Key Views to Implement:**

**1. The Global Pulse (NOC Video Wall)**
* **Layout:** A masonry or grid layout of critical KPIs.
* **Components:**
    * Large, high-contrast numeric tiles for: Active Streams, Global Egress (Gbps), Healthy Engines, and Success Rate (%).
    * *Mini-Sparklines* embedded directly under the KPI numbers showing the last 15 minutes of trend data.
    * An "Active Emergencies" banner (using ShadCN `Alert`) that only appears if the Provisioning Circuit Breaker is tripped or a VPN tunnel is down.

**2. The Routing Topology (Network Flow Map)**
* **Implementation:** Use **React Flow**.
* **Concept:** Visualize the actual data pipeline. 
    * *Nodes:* VPN 1 / VPN 2 -> Individual Docker Engines -> The Mux/Proxy -> Client connections.
    * *Edges:* Animate the edges based on active bandwidth. If a VPN fails, visually turn the node red and animate the failover traffic routing to the secondary VPN.

**3. The Stream Microscope (Deep QoE Analytics)**
* **Implementation:** Use **Apache ECharts**.
* **Concept:** Replace basic text metrics with visual correlations.
    * *Buffer Heatmap:* A matrix where the Y-axis is "Active Streams", the X-axis is a rolling 5-minute time window, and the color intensity represents "Buffer Avg Pieces" (Red = stuttering/empty, Green = healthy buffer).
    * *Latency Overlay:* A multi-axis chart showing "TTFB p95" overlaid with vertical annotation lines marking literal "Engine Container Start" events to correlate scaling actions with latency spikes.

**4. The Fleet Matrix (Infrastructure Saturation)**
* **Implementation:** CSS Grid / Flexbox + ShadCN Tooltips.
* **Concept:** A "Honeycomb" or block grid. Do not just use a list for engines. Display every running Docker container as a colored block.
    * Color = Current CPU/RAM utilization.
    * Hovering over a block reveals a ShadCN `HoverCard` showing its specific host port, assigned VPN tunnel, uptime, and restart count (Pedigree).
    * Clicking a block opens a ShadCN `Sheet` with live trailing Docker logs.

**Initial Output Required:**
Start by scaffolding the exact TypeScript interfaces/types required based on your knowledge of the AceStream Orchestrator backend. Then, generate the complete React code for **"The Routing Topology"** using React Flow and ShadCN, mocking realistic VPN-to-Engine-to-Proxy node data.
