import uuid
import datetime
import json
import queue
import threading
import time
from typing import Any, Dict, Optional

# ==========================================
# 1. THE PROTOCOL: Sentinel Message Packet (SMP)
# ==========================================
class SMPPacket:
    """The standardized communication unit for all agents."""
    def __init__(self, sender: str, payload: Dict[str, Any], trace_id: str = None):
        self.packet_id = str(uuid.uuid4())
        self.timestamp = datetime.datetime.now().isoformat()
        self.sender = sender
        self.trace_id = trace_id or str(uuid.uuid4())
        self.payload = payload

    def to_json(self):
        return json.dumps(self.__dict__, indent=2)

    @classmethod
    def from_dict(cls, data: Dict):
        packet = cls(data['sender'], data['payload'], data['trace_id'])
        packet.packet_id = data['packet_id']
        packet.timestamp = data['timestamp']
        return packet

# ==========================================
# 2. THE KERNEL: Message Bus & Orchestrator
# ==========================================
class MessageBus:
    """The central nervous system. Routes packets to registered agents."""
    def __init__(self):
        self.agents = {}
        self.queue = queue.Queue()
        self.running = True

    def register_agent(self, agent_name: str, agent_instance: 'BaseAgent'):
        self.agents[agent_name] = agent_instance
        print(f"[KERNEL] Agent '{agent_name}' registered to Bus.")

    def publish(self, packet: SMPPacket):
        print(f"[BUS] Packet {packet.packet_id} sent by {packet.sender}")
        self.queue.put(packet)

    def start_routing(self):
        def route_loop():
            while self.running:
                try:
                    packet = self.queue.get(timeout=1)
                    # FIX 1: was self.agents._registry.items() -- _registry does not exist
                    for name, agent in self.agents.items():
                        threading.Thread(target=agent.receive, args=(packet,)).start()
                except queue.Empty:
                    continue

        threading.Thread(target=route_loop, daemon=True).start()

class Orchestrator:
    """The Brain. Manages the State Machine and System Health."""
    def __init__(self, bus: MessageBus):
        self.bus = bus
        self.state = "IDLE"
        self.registry = {}

    def set_agent_registry(self, agents: Dict[str, 'BaseAgent']):
        self.agents_registry = agents

    def transition(self, new_state: str):
        print(f"[ORCHESTRATOR] State Transition: {self.state} -> {new_state}")
        self.state = new_state

# ==========================================
# 3. THE AGENT BLUEPRINT
# ==========================================
class BaseAgent:
    """The blueprint for all specialized agents."""
    def __init__(self, name: str, bus):
        self.name = name
        self.bus = bus

    def receive(self, packet: SMPPacket):
        if self.should_process(packet):
            self.process(packet)

    def should_process(self, packet: SMPPacket) -> bool:
        # Default: process nothing -- subclasses must override
        # FIX 2: returning True here caused every agent to process every packet,
        # creating an infinite broadcast loop
        return False

    def process(self, packet: SMPPacket):
        raise NotImplementedError("Each agent must implement its own process logic.")

# ==========================================
# 4. CONCRETE AGENT IMPLEMENTATIONS (The Workers)
# ==========================================

class IngestAgent(BaseAgent):
    """Task: Scrape data and publish HTML."""

    def should_process(self, packet: SMPPacket) -> bool:
        # Only process packets that contain a URL to scrape
        return "url" in packet.payload

    def process(self, packet: SMPPacket):
        print(f"[{self.name}] Processing Ingest Request...")
        mock_html = "<html><body><h1>Job: Python Dev</h1></body></html>"
        new_packet = SMPPacket(
            sender=self.name,
            payload={"html_content": mock_html, "source_url": packet.payload.get("url")},
            trace_id=packet.trace_id
        )
        self.bus.publish(new_packet)

class ParseAgent(BaseAgent):
    """Task: Convert HTML to JSON."""

    def should_process(self, packet: SMPPacket) -> bool:
        # Only process packets that contain raw HTML content
        return "html_content" in packet.payload

    def process(self, packet: SMPPacket):
        print(f"[{self.name}] Parsing HTML content...")
        html = packet.payload.get("html_content", "")
        parsed_data = {"job_title": "Python Dev"} if "Python" in html else {"error": "Parse failed"}
        # FIX 3: removed redundant SMP_Packet_Creator wrapper
        new_packet = SMPPacket(
            sender=self.name,
            payload=parsed_data,
            trace_id=packet.trace_id
        )
        self.bus.publish(new_packet)

class QAAgent(BaseAgent):
    """The Quality Assurance Agent - The Enforcer."""

    def should_process(self, packet: SMPPacket) -> bool:
        # Only process packets that contain parsed job data
        return "job_title" in packet.payload or "error" in packet.payload

    def process(self, packet: SMPPacket):
        data = packet.payload
        if "job_title" in data:
            print(f"[QA] SUCCESS: Packet validated.")
        else:
            print(f"[QA] FAILURE: Schema violation! Alerting System...")

# =================================================================
# EXECUTION SIMULATION
# =================================================================

if __name__ == "__main__":

    class SimpleBus:
        def __init__(self):
            self.agents = []

        def register(self, agent):
            self.agents.append(agent)

        def publish(self, packet):
            for agent in self.agents:
                threading.Thread(target=agent.receive, args=(packet,)).start()

    system_bus = SimpleBus()

    # FIX 4: agents require name and bus arguments
    ingest_agent = IngestAgent(name="IngestAgent", bus=system_bus)
    parse_agent  = ParseAgent(name="ParseAgent",  bus=system_bus)
    qa_agent     = QAAgent(name="QAAgent",        bus=system_bus)

    system_bus.register(ingest_agent)
    system_bus.register(parse_agent)
    system_bus.register(qa_agent)

    print("--- STARTING WORKFLOW ---")
    # FIX 5: was SMPacket (typo) -- correct class name is SMPPacket
    initial_task = SMPPacket(sender="User", payload={"url": "http://jobs.com/1"})
    system_bus.publish(initial_task)

    # Allow threads to complete before exit
    time.sleep(1)
    print("--- WORKFLOW COMPLETE ---")