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
 - [ ] starts in service mode without command-line arguments
 - [ ] keeps retrying after a non-authentication CONNACK failure
 - [ ] exits with code 2 after an authentication CONNACK failure
 - [ ] rejects JSON configuration which does not conform to the installed schema with code 6

wb-diag-collect specific
------------------------

 - [ ] service responds to 'Collect' request from Web UI and gives `.zip` file
 - [ ] service responds after Mosquitto restart
 - [ ] command creates `.zip` file on console run (`wb-diag-collect diag`)
 - [ ] zip file is valid and contains actual information
 - [ ] zip file should be less than 1 MB
