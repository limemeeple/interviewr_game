// 모든 axios 요청에 토큰을 자동으로 붙임
axios.interceptors.request.use(config => {
    const token = sessionStorage.getItem("token");
    if (token) config.headers.Authorization = `Bearer ${token}`;
    return config;
});

// 로그인이 풀렸으면(401) 로그인 화면으로
axios.interceptors.response.use(
    res => res,
    err => {
        if (err.response && err.response.status === 401) {
            alert(err.response.data.detail);
            location.href = "/";
        }
        return Promise.reject(err);
    }
);