return {
    index: getDevices().indexOf("PTB_TEST_PC1"),
    hasPc: getDevices().indexOf("PTB_TEST_PC1") >= 0,
    info: getDevices().indexOf("PTB_TEST_PC1") >= 0 ? getDeviceInfo("PTB_TEST_PC1") : null
};
