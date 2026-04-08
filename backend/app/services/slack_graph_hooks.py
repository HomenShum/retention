"""Slack Graph Hooks — populate the context graph during OpenClaw agent interactions.

The OpenClaw Slack agent currently has zero memory between messages.
Every @OpenClaw retention.sh starts cold — no knowledge of what was asked yesterday,
what deep sims concluded, what transcriptions were done.

These hooks change that by recording each conversation as a connected graph:
  user request → context gathered → intent classified → agent routed →
  tools called → response posted → user feedback

With this, the agent can answer:
  - "Homin asked about this competitor 3 times — consensus evolved from X to Y"
  - "This YouTube video was transcribed and the key insight was applied to slide 06d"
  - "The last deep sim on this topic had 4 action items, 2 are still open"
  - "Khush asked about rerun logic — the answer is in thread 1774028293"

Usage:
    from .slack_graph_hooks import SlackGraphHooks

    hooks = SlackGraphHooks(session_id="slack_thread_123")

    # When user sends a message
    req_id = hooks.on_user_message(text, user_id, channel, thread_ts)

    # When context is gathered
    hooks.on_context_gathered(req_id, context_type, summary, sources)

    # When intent is classified
    hooks.on_intent_classified(req_id, intent, confidence)

    # When agent is routed
    hooks.on_agent_routed(req_id, agent_name, reason)

    # When tool is called
    hooks.on_tool_called(req_id, tool_name, params, result_summary, status, duration_ms)

    # When response is posted
    hooks.on_response_posted(req_id, response_text, channel, thread_ts)

    # When user reacts
    hooks.on_user_feedback(req_id, feedback_type, content, user_id)

    # Persist
    hooks.save()

Query helpers:
    hooks.get_topic_history(topic_keywords) → past conversations on topic
    hooks.get_user_history(user_id) → what this user has asked
    hooks.get_open_action_items() → unresolved items from deep sims
    hooks.find_similar_request(new_message) → precedent conversations
"""

import logging
from typing import Any, Dict, List, Optional

from ..agents.qa_pipeline.context_graph import (
    ContextGraph,
    ContextGraphManager,
    EdgeType,
    GraphNode,
    NodeType,
    conversation_fingerprint,
    make_classification_node,
    make_context_node,
    make_feedback_node,
    make_request_node,
    make_response_node,
    make_tool_call_node,
)

logger = logging.getLogger(__name__)

# The Slack agent uses a single shared graph (not per-app)
_SLACK_GRAPH_ID = "openclaw_slack"


class SlackGraphHooks:
    """Hooks that fire during Slack agent interactions to build the context graph.

    Each user message becomes a REQUEST node. As the agent processes it,
    CONTEXT, CLASSIFICATION, ROUTING, TOOL_CALL, and RESPONSE nodes are
    added and linked. User reactions become FEEDBACK nodes.

    The graph is shared across all conversations (single graph for OpenClaw),
    enabling cross-conversation memory and precedent matching.
    """

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or "slack_session"

        mgr = ContextGraphManager.get()
        # Use global graph for Slack (cross-conversation memory)
        self.graph = mgr.global_graph

        # Track request chain for wiring
        self._request_node_map: Dict[str, str] = {}  # req_id → node_id
        self._last_node_for_request: Dict[str, str] = {}  # req_id → last node_id

    # ── Event Hooks ──────────────────────────────────────────────────────

    def on_user_message(
        self,
        text: str,
        user_id: str,
        channel: str,
        thread_ts: str = "",
    ) -> str:
        """Called when a user sends a message to OpenClaw.

        Creates a REQUEST node and checks for precedent conversations.
        Returns the request node ID (use as req_id for subsequent hooks).
        """
        node = make_request_node(
            user_message=text,
            user_id=user_id,
            channel=channel,
            thread_ts=thread_ts,
            session_id=self.session_id,
        )
        self.graph.add_node(node)

        self._request_node_map[node.node_id] = node.node_id
        self._last_node_for_request[node.node_id] = node.node_id

        # Check for precedent: has a similar question been asked before?
        fp = conversation_fingerprint(text)
        precedent = self.graph.find_by_fingerprint(fp)
        if precedent and precedent.node_id != node.node_id:
            self.graph.connect(
                node.node_id,
                precedent.node_id,
                EdgeType.REQUEST_SUPERSEDES,
                data={"relationship": "similar_prior_request"},
            )
            logger.info(f"Found precedent for request: {precedent.label[:40]}")

        # Link to previous message in same thread
        if thread_ts:
            thread_requests = [
                n for n in self.graph.get_nodes_by_type(NodeType.REQUEST)
                if n.data.get("thread_ts") == thread_ts
                and n.node_id != node.node_id
            ]
            if thread_requests:
                # Link to most recent in thread
                latest = max(thread_requests, key=lambda n: n.created_at)
                self.graph.connect(
                    node.node_id,
                    latest.node_id,
                    EdgeType.REQUEST_SUPERSEDES,
                    data={"relationship": "thread_continuation"},
                )

        logger.info(f"Graph: user message from {user_id} in {channel}")
        return node.node_id

    def on_context_gathered(
        self,
        req_id: str,
        context_type: str,
        summary: str,
        sources: Optional[List[str]] = None,
    ) -> str:
        """Called when the agent gathers context for a request.

        context_type examples: "thread_history", "channel_search",
        "file_content", "youtube_transcript", "deep_sim_result",
        "prior_conversation"
        """
        node = make_context_node(
            context_type=context_type,
            summary=summary,
            session_id=self.session_id,
            sources=sources,
        )
        self.graph.add_node(node)

        # Wire: request → gathered context
        self.graph.connect(
            req_id,
            node.node_id,
            EdgeType.REQUEST_GATHERED_CONTEXT,
        )

        self._last_node_for_request[req_id] = node.node_id
        return node.node_id

    def on_intent_classified(
        self,
        req_id: str,
        intent: str,
        confidence: float = 0.0,
        reasoning: str = "",
    ) -> str:
        """Called when the agent classifies the user's intent.

        intent examples: "qa_pipeline", "search", "deep_sim",
        "transcribe", "summarize", "code_review", "action_item"
        """
        node = make_classification_node(
            intent=intent,
            confidence=confidence,
            session_id=self.session_id,
            reasoning=reasoning,
        )
        self.graph.add_node(node)

        # Wire: request → classified as
        self.graph.connect(
            req_id,
            node.node_id,
            EdgeType.REQUEST_CLASSIFIED_AS,
        )

        self._last_node_for_request[req_id] = node.node_id
        return node.node_id

    def on_agent_routed(
        self,
        req_id: str,
        agent_name: str,
        reason: str = "",
    ) -> str:
        """Called when the request is routed to a specialist agent.

        agent_name examples: "search_agent", "device_testing_agent",
        "test_generation_agent", "deep_sim_agent", "coordinator"
        """
        node = GraphNode(
            node_type=NodeType.ROUTING,
            label=f"→ {agent_name}",
            data={
                "agent_name": agent_name,
                "reason": reason,
            },
            run_id=self.session_id,
        )
        self.graph.add_node(node)

        # Wire from last node (usually classification)
        last = self._last_node_for_request.get(req_id)
        if last:
            self.graph.connect(
                last,
                node.node_id,
                EdgeType.CLASSIFICATION_ROUTED_TO,
            )

        self._last_node_for_request[req_id] = node.node_id
        return node.node_id

    def on_tool_called(
        self,
        req_id: str,
        tool_name: str,
        params: Dict[str, Any],
        result_summary: str = "",
        status: str = "success",
        duration_ms: Optional[int] = None,
    ) -> str:
        """Called when an MCP tool is invoked during processing."""
        node = make_tool_call_node(
            tool_name=tool_name,
            params=params,
            result_summary=result_summary,
            session_id=self.session_id,
            status=status,
            duration_ms=duration_ms,
        )
        self.graph.add_node(node)

        # Wire from last node (routing or previous tool call)
        last = self._last_node_for_request.get(req_id)
        if last:
            self.graph.connect(
                last,
                node.node_id,
                EdgeType.ROUTING_CALLED_TOOL,
            )

        self._last_node_for_request[req_id] = node.node_id
        return node.node_id

    def on_response_posted(
        self,
        req_id: str,
        response_text: str,
        channel: str = "",
        thread_ts: str = "",
    ) -> str:
        """Called when the agent posts a response to Slack."""
        node = make_response_node(
            response_text=response_text,
            session_id=self.session_id,
            channel=channel,
            thread_ts=thread_ts,
        )
        self.graph.add_node(node)

        # Wire from last node (tool call or routing)
        last = self._last_node_for_request.get(req_id)
        if last:
            self.graph.connect(
                last,
                node.node_id,
                EdgeType.TOOL_PRODUCED_RESPONSE,
            )

        self._last_node_for_request[req_id] = node.node_id
        return node.node_id

    def on_user_feedback(
        self,
        req_id: str,
        feedback_type: str,
        content: str,
        user_id: str = "",
    ) -> str:
        """Called when a user reacts to or follows up on a response.

        feedback_type examples: "reaction_positive", "reaction_negative",
        "follow_up", "correction", "approval", "rejection"
        """
        node = make_feedback_node(
            feedback_type=feedback_type,
            content=content,
            session_id=self.session_id,
            user_id=user_id,
        )
        self.graph.add_node(node)

        # Wire from last node (response)
        last = self._last_node_for_request.get(req_id)
        if last:
            self.graph.connect(
                last,
                node.node_id,
                EdgeType.RESPONSE_GOT_FEEDBACK,
            )

        return node.node_id

    # ── Query Helpers — Cross-Conversation Memory ────────────────────────

    def get_topic_history(self, keywords: List[str], limit: int = 10) -> List[Dict[str, Any]]:
        """Find past conversations matching topic keywords.

        Searches REQUEST nodes by keyword overlap in message text.
        Returns most recent matches with their full chain.
        """
        results = []
        requests = self.graph.get_nodes_by_type(NodeType.REQUEST)

        kw_lower = [kw.lower() for kw in keywords]

        for req in requests:
            msg = req.data.get("full_message", req.label).lower()
            if any(kw in msg for kw in kw_lower):
                # Get the response for this request
                chain = self._get_request_chain(req.node_id)
                results.append({
                    "request_id": req.node_id,
                    "message": req.data.get("full_message", req.label),
                    "user_id": req.data.get("user_id"),
                    "channel": req.data.get("channel"),
                    "thread_ts": req.data.get("thread_ts"),
                    "created_at": req.created_at,
                    "chain_summary": chain,
                })

        # Sort by recency
        results.sort(key=lambda x: x["created_at"], reverse=True)
        return results[:limit]

    def get_user_history(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get all requests from a specific user."""
        results = []
        for req in self.graph.get_nodes_by_type(NodeType.REQUEST):
            if req.data.get("user_id") == user_id:
                results.append({
                    "request_id": req.node_id,
                    "message": req.data.get("full_message", req.label),
                    "channel": req.data.get("channel"),
                    "created_at": req.created_at,
                })

        results.sort(key=lambda x: x["created_at"], reverse=True)
        return results[:limit]

    def get_open_action_items(self) -> List[Dict[str, Any]]:
        """Find unresolved action items from deep sims and other interactions.

        Looks for CONTEXT nodes with type "deep_sim_result" or "action_item"
        that don't have a CONTEXT_RESOLVED_BY edge.
        """
        items = []
        for node in self.graph.get_nodes_by_type(NodeType.CONTEXT):
            ctx_type = node.data.get("context_type", "")
            if ctx_type not in ("deep_sim_result", "action_item"):
                continue

            # Check if resolved
            resolved = self.graph.get_outgoing(node.node_id, EdgeType.CONTEXT_RESOLVED_BY)
            if not resolved:
                items.append({
                    "node_id": node.node_id,
                    "context_type": ctx_type,
                    "summary": node.data.get("summary", node.label),
                    "created_at": node.created_at,
                    "sources": node.data.get("sources", []),
                })

        return items

    def find_similar_request(self, new_message: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Find past requests similar to a new message.

        Uses fingerprint prefix matching for fast similarity search.
        """
        fp = conversation_fingerprint(new_message)
        precedents = self.graph.find_precedents(fp, NodeType.REQUEST, limit)

        return [
            {
                "node_id": p.node_id,
                "message": p.data.get("full_message", p.label),
                "user_id": p.data.get("user_id"),
                "channel": p.data.get("channel"),
                "created_at": p.created_at,
                "similarity": "fingerprint_prefix_match",
            }
            for p in precedents
        ]

    def get_conversation_summary(self, thread_ts: str) -> Dict[str, Any]:
        """Get a summary of an entire conversation thread."""
        requests = [
            n for n in self.graph.get_nodes_by_type(NodeType.REQUEST)
            if n.data.get("thread_ts") == thread_ts
        ]

        if not requests:
            return {"thread_ts": thread_ts, "messages": 0, "summary": "No messages found"}

        # Collect all nodes in the thread
        tool_calls = []
        responses = []
        feedback = []

        for req in requests:
            chain = self._get_request_chain(req.node_id)
            tool_calls.extend(chain.get("tools", []))
            if chain.get("response"):
                responses.append(chain["response"])
            feedback.extend(chain.get("feedback", []))

        return {
            "thread_ts": thread_ts,
            "messages": len(requests),
            "users": list({r.data.get("user_id") for r in requests}),
            "tool_calls": len(tool_calls),
            "tools_used": list({t.get("tool_name") for t in tool_calls}),
            "responses": len(responses),
            "feedback_count": len(feedback),
            "first_message": min(r.created_at for r in requests),
            "last_message": max(r.created_at for r in requests),
        }

    # ── Internal Helpers ─────────────────────────────────────────────────

    def _get_request_chain(self, req_id: str) -> Dict[str, Any]:
        """Walk the graph forward from a request to get its full processing chain."""
        chain: Dict[str, Any] = {
            "contexts": [],
            "intent": None,
            "agent": None,
            "tools": [],
            "response": None,
            "feedback": [],
        }

        visited = set()

        def _walk(nid: str):
            if nid in visited:
                return
            visited.add(nid)
            node = self.graph.get_node(nid)
            if not node:
                return

            if node.node_type == NodeType.CONTEXT:
                chain["contexts"].append({
                    "type": node.data.get("context_type"),
                    "summary": node.data.get("summary"),
                })
            elif node.node_type == NodeType.CLASSIFICATION:
                chain["intent"] = node.data.get("intent")
            elif node.node_type == NodeType.ROUTING:
                chain["agent"] = node.data.get("agent_name")
            elif node.node_type == NodeType.TOOL_CALL:
                chain["tools"].append({
                    "tool_name": node.data.get("tool_name"),
                    "status": node.data.get("status"),
                    "result_summary": node.data.get("result_summary"),
                })
            elif node.node_type == NodeType.RESPONSE:
                chain["response"] = node.data.get("full_response", node.label)
            elif node.node_type == NodeType.FEEDBACK:
                chain["feedback"].append({
                    "type": node.data.get("feedback_type"),
                    "content": node.data.get("content"),
                })

            for edge, target in self.graph.get_outgoing(nid):
                _walk(target.node_id)

        _walk(req_id)
        return chain

    # ── Persistence ──────────────────────────────────────────────────────

    def save(self) -> None:
        """Persist the graph."""
        ContextGraphManager.get().save_all()

    def get_stats(self) -> Dict[str, Any]:
        """Get graph stats."""
        return {
            "session_id": self.session_id,
            "graph_stats": self.graph.stats(),
            "total_requests": len(self.graph.get_nodes_by_type(NodeType.REQUEST)),
            "total_responses": len(self.graph.get_nodes_by_type(NodeType.RESPONSE)),
            "total_tool_calls": len(self.graph.get_nodes_by_type(NodeType.TOOL_CALL)),
            "total_feedback": len(self.graph.get_nodes_by_type(NodeType.FEEDBACK)),
        }


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_slack_hooks(session_id: Optional[str] = None) -> SlackGraphHooks:
    """Create Slack graph hooks for a conversation session."""
    return SlackGraphHooks(session_id=session_id)


__all__ = ["SlackGraphHooks", "create_slack_hooks"]
