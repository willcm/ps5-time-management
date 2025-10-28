ARG BUILD_FROM
FROM ${BUILD_FROM}

# Build arguments
ARG BUILD_ARCH
ARG BUILD_DATE
ARG BUILD_DESCRIPTION
ARG BUILD_NAME
ARG BUILD_REF
ARG BUILD_REPOSITORY
ARG BUILD_VERSION

# Labels
LABEL \
    io.hass.name="${BUILD_NAME}" \
    io.hass.description="${BUILD_DESCRIPTION}" \
    io.hass.arch="${BUILD_ARCH}" \
    io.hass.type="addon" \
    io.hass.version=${BUILD_VERSION} \
    maintainer="Will"

# Install system dependencies
RUN apk add --no-cache \
    python3 \
    py3-pip \
    py3-setuptools \
    sqlite

# Set working directory
WORKDIR /app

# Copy requirements and install Python dependencies
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application files
COPY . ./

# Make sure scripts are executable
RUN chmod a+x run.sh

# Set up data directory
VOLUME [ "/data" ]

# Run the application
CMD [ "python3", "main.py" ]

