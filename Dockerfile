FROM python:alpine

MAINTAINER Adam Kocoloski kocolosk@apache.org

RUN pip install requests dnspython

COPY mem3_helper.py /opt/mem3_helper/

WORKDIR /opt/mem3_helper

CMD ["mem3_helper.py"]
ENTRYPOINT ["python"]
