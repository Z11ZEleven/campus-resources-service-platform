@echo off
docker exec -it campus-resources-mysql-1 sh -c "mysql -u$MYSQL_USER -p$MYSQL_PASSWORD $MYSQL_DATABASE"
