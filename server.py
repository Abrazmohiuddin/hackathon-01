import socket
import threading
import json
import time
import uuid
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# --- ARCHITECTURE STATE ---
message_history = []
my_node_id = str(uuid.uuid4())
my_current_username = "Anonymous" 
last_peer_seen = 0
seen_messages = set()
relay_buffer = [] 

# --- THE LISTENER & RELAY LOGIC ---
def udp_listener():
    global last_peer_seen
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(('0.0.0.0', 5555))
    
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            packet = json.loads(data.decode('utf-8'))
            
            # Handle Heartbeats
            if packet.get("type") == "heartbeat":
                if packet.get("node_id") != my_node_id:
                    last_peer_seen = time.time()
                    if relay_buffer:
                        # Flush the backpack if a peer is near
                        relay_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                        relay_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                        for msg in relay_buffer:
                            relay_sock.sendto(json.dumps(msg).encode('utf-8'), ('255.255.255.255', 5555))
                        relay_sock.close()
                continue
            
            # Handle Chat Messages
            msg_id = packet.get("msg_id")
            if msg_id and msg_id not in seen_messages:
                seen_messages.add(msg_id)
                
                # --- TTL & HOP LOGIC ---
                packet["ttl"] -= 1
                packet["hop_count"] += 1
                
                if packet["ttl"] <= 0:
                    print(f"[!] Packet {msg_id} reached end of life. Dropping.")
                    continue

                target = packet.get("target", "ALL")
                if target == "ALL" or target.lower() == my_current_username.lower():
                    message_history.append({
                        "username": packet["username"],
                        "text": packet["text"],
                        "is_dm": target != "ALL",
                        "target": target,
                        "hops": packet["hop_count"] # Displayed in UI
                    })
                
                # RE-BROADCAST (The Jump)
                relay_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                relay_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                relay_sock.sendto(json.dumps(packet).encode('utf-8'), ('255.255.255.255', 5555))
                relay_sock.close()
                
        except Exception as e:
            print(f"Network Error: {e}") # Improved Debugging

# --- HEARTBEAT ---
def heartbeat_emitter():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        try:
            payload = json.dumps({"type": "heartbeat", "node_id": my_node_id})
            sock.sendto(payload.encode('utf-8'), ('255.255.255.255', 5555))
        except Exception as e:
            print(f"Broadcast Error: {e}")
        time.sleep(2)

@app.route('/')
def home(): return render_template('index.html')

@app.route('/messages')
def get_messages(): return jsonify(message_history)

@app.route('/status')
def get_status(): return jsonify({"connected": (time.time() - last_peer_seen) < 6})

@app.route('/send', methods=['POST'])
def send_message():
    global my_current_username
    data = request.json
    raw_text = data.get('message', '').strip()
    my_current_username = data.get('username', 'Anonymous').strip()

    if raw_text:
        new_msg_id = str(uuid.uuid4())
        seen_messages.add(new_msg_id)
        
        target_user = "ALL"
        msg_text = raw_text
        if raw_text.startswith("@"):
            parts = raw_text.split(" ", 1)
            if len(parts) > 1:
                target_user = parts[0][1:]; msg_text = parts[1]
        
        # --- NEW PACKET STRUCTURE ---
        payload = {
            "type": "chat",
            "msg_id": new_msg_id,
            "username": my_current_username,
            "target": target_user,
            "text": msg_text,
            "ttl": 5,        # Maximum 5 jumps
            "hop_count": 0   # Starts at 0
        }
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(json.dumps(payload).encode('utf-8'), ('255.255.255.255', 5555))
        sock.close()
        
        message_history.append({
            "username": my_current_username,
            "text": msg_text,
            "is_dm": target_user != "ALL",
            "target": target_user,
            "hops": 0
        })
        return jsonify({"status": "Sent"})
    return jsonify({"error": "Empty"}), 400

if __name__ == '__main__':
    threading.Thread(target=udp_listener, daemon=True).start()
    threading.Thread(target=heartbeat_emitter, daemon=True).start()
    app.run(host='0.0.0.0', port=5000)