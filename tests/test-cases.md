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
 - [ ] waits for Mosquitto instead of exiting when started while Mosquitto is stopped
 - [ ] logs one error and still exits with code 0 when stopped while Mosquitto is stopped
 - [ ] cancels a running collection and kills its child processes on SIGINT/SIGTERM
 - [ ] exits with code 6 on a missing or invalid config and is not restarted by systemd

wb-diag-collect specific
------------------------

 - [ ] service responds to 'Collect' request from Web UI and gives `.zip` file
 - [ ] service responds after Mosquitto restart
 - [ ] command creates `.zip` file on console run (`wb-diag-collect diag`)
 - [ ] zip file is valid and contains actual information
 - [ ] zip file should be less than 1 MB
