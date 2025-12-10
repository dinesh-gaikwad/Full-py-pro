<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Chat Room</title>
  </head>
  <body>
    <h2>Room: {{ room_name }}</h2>
    <div id="chat-log" style="height:300px;overflow:auto;border:1px solid #ccc;padding:8px"></div>
    <input id="chat-message-input" type="text" size="80" />
    <button id="chat-message-send">Send</button>

    <script>
      const roomName = "{{ room_name }}";
      const ws_scheme = window.location.protocol === "https:" ? "wss" : "ws";
      const chatSocket = new WebSocket(ws_scheme + '://' + window.location.host + '/ws/chat/' + roomName + '/');

      chatSocket.onmessage = function(e) {
        const data = JSON.parse(e.data);
        const log = document.getElementById('chat-log');
        log.innerHTML += '<b>' + data.username + ':</b> ' + data.message + '<br>';
        log.scrollTop = log.scrollHeight;
      };

      document.getElementById('chat-message-send').onclick = function() {
        const input = document.getElementById('chat-message-input');
        const msg = input.value;
        chatSocket.send(JSON.stringify({'message': msg}));
        input.value = '';
      };
    </script>
  </body>
</html>
