import os
import socket
import threading
import json
import time
import uuid
import urllib.request # NEW: For talking to local Ollama
from flask import Flask, render_template, request, jsonify

# --- BULLETPROOF FOLDER FIX ---
base_dir = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(base_dir, 'templates')
app = Flask(__name__, template_folder=template_dir)

# --- ARCHITECTURE STATE ---
message_history = []
my_node_id = str(uuid.uuid4())
my_current_username = "Anonymous"
last_peer_seen = 0
seen_messages = set()
relay_buffer = []

# The security key that isolates this company's network
current_network_key = None 

# --- EDGE AI COMPUTE NODE (OLLAMA INTERCEPTOR) ---
def query_ollama(prompt, network_key, sender_username):
    print(f"[*] AI Triggered by @{sender_username}. Generating local response...")
    
    url = "http://localhost:11434/api/generate"
    # CHANGE "llama3" BELOW TO WHATEVER MODEL YOU DOWNLOADED (e.g., "phi3", "mistral")
    data = {"model": "llama3", "prompt": prompt, "stream": False} 
    
    try:
        req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers={'Content-Type': 'application/json'})
        response = urllib.request.urlopen(req, timeout=60)
        result = json.loads(response.read().decode('utf-8'))
        ai_reply = result.get('response', 'Error formatting AI response.')
        
        # Package the AI's response and shoot it back into the mesh
        ai_msg_id = str(uuid.uuid4())
        seen_messages.add(ai_msg_id)
        
        payload = {
            "type": "chat",
            "msg_id": ai_msg_id,
            "network_key": network_key,
            "username": "NEXUS-AI",
            "target": sender_username, # Target the user who asked
            "text": ai_reply,
            "ttl": 5,
            "hop_count": 0
        }
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(json.dumps(payload).encode('utf-8'), ('255.255.255.255', 5555))
        sock.close()
        print(f"[+] AI Response successfully routed to @{sender_username}.")
        
    except Exception as e:
        # If Ollama is NOT running on this specific laptop, fail silently. 
        # This is genius because it allows only the "Heavy" laptops to act as the Brain.
        pass

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
            
            # --- SECURITY GATEWAY ---
            packet_key = packet.get("network_key")
            if not current_network_key or packet_key != current_network_key:
                continue

            # Handle Heartbeats
            if packet.get("type") == "heartbeat":
                if packet.get("node_id") != my_node_id:
                    last_peer_seen = time.time()
                    if relay_buffer:
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
                
                packet["ttl"] -= 1
                packet["hop_count"] += 1
                if packet["ttl"] <= 0: continue

                target = packet.get("target", "ALL")
                
                # --- NEW: INTERCEPT MESSAGES FOR THE AI ---
                if target.upper() == "NEXUS-AI":
                    # We spin this off into a background thread so the whole mesh doesn't freeze while the AI thinks
                    threading.Thread(target=query_ollama, args=(packet["text"], packet["network_key"], packet["username"]), daemon=True).start()

                # Process normal messages
                if target == "ALL" or target.lower() == my_current_username.lower():
                    message_history.append({
                        "username": packet["username"],
                        "text": packet["text"],
                        "is_dm": target != "ALL",
                        "target": target,
                        "hops": packet["hop_count"]
                    })
                elif target != "ALL":
                    if packet not in relay_buffer:
                        relay_buffer.append(packet)
                        if len(relay_buffer) > 50: relay_buffer.pop(0) 
                
                # RE-BROADCAST (The Jump)
                relay_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                relay_sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                relay_sock.sendto(json.dumps(packet).encode('utf-8'), ('255.255.255.255', 5555))
                relay_sock.close()
                
        except Exception as e:
            pass

# --- HEARTBEAT ---
def heartbeat_emitter():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    while True:
        if current_network_key:
            try:
                payload = json.dumps({"type": "heartbeat", "node_id": my_node_id, "network_key": current_network_key})
                sock.sendto(payload.encode('utf-8'), ('255.255.255.255', 5555))
            except: pass
        time.sleep(2)

@app.route('/')
def home(): return render_template('index.html')

@app.route('/messages')
def get_messages(): return jsonify(message_history)

@app.route('/status')
def get_status(): return jsonify({"connected": (time.time() - last_peer_seen) < 6})

@app.route('/join_network', methods=['POST'])
def join_network():
    global current_network_key, my_current_username
    data = request.json
    company_name = data.get('company', '').strip().upper()
    security_key = data.get('key', '').strip()
    my_current_username = data.get('username', 'Anonymous').strip()
    current_network_key = f"{company_name}::{security_key}"
    return jsonify({"status": "Network Configured", "company": company_name})

@app.route('/send', methods=['POST'])
def send_message():
    global my_current_username, current_network_key
    data = request.json
    raw_text = data.get('message', '').strip()
    
    if not current_network_key: return jsonify({"error": "Not joined to a network"}), 403

    if raw_text:
        new_msg_id = str(uuid.uuid4())
        seen_messages.add(new_msg_id)
        
        target_user = "ALL"
        msg_text = raw_text
        if raw_text.startswith("@"):
            parts = raw_text.split(" ", 1)
            if len(parts) > 1:
                target_user = parts[0][1:]; msg_text = parts[1]
        
        payload = {
            "type": "chat", "msg_id": new_msg_id, "network_key": current_network_key, 
            "username": my_current_username, "target": target_user, "text": msg_text,
            "ttl": 5, "hop_count": 0   
        }
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(json.dumps(payload).encode('utf-8'), ('255.255.255.255', 5555))
        sock.close()
        
        message_history.append({
            "username": my_current_username, "text": msg_text,
            "is_dm": target_user != "ALL", "target": target_user, "hops": 0
        })
        return jsonify({"status": "Sent"})
    return jsonify({"error": "Empty"}), 400

if __name__ == '__main__':
    threading.Thread(target=udp_listener, daemon=True).start()
    threading.Thread(target=heartbeat_emitter, daemon=True).start()
    print("\n[*] SERVER ONLINE: Open http://127.0.0.1:5000 in your browser\n")
    app.run(host='0.0.0.0', port=5000, debug=False)
