#!/bin/bash
export DB_HOST="${db_host}"
export DB_NAME="${db_name}"
systemctl start app
