wb-diag-collect test cases
==========================

Common
------

Service (`systemctl status wb-diag-collect.service`):

 - [ ] is up after installation
 - [ ] does not consume all CPU after 10 seconds after start
 - [ ] does not consume all CPU after 10 seconds after Mosquitto restart
 - [ ] publishes its RPC endpoints on start
 - [ ] publishes its RPC endpoints after Mosquitto restart
 - [ ] removes its RPC endpoints on stop

wb-diag-collect specific
------------------------

 - [ ] service responds to 'Collect' request from Web UI and gives `.zip` file
 - [ ] service responds after Mosquitto restart
 - [ ] command creates `.zip` file on console run (`wb-diag-collect diag`)
