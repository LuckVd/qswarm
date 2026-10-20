/* mutated seed=8185bd58 */
/**
 * OWASP Benchmark Project v1.2
 *
 * <p>This file is part of the Open Web Application Security Project (OWASP) Benchmark Project. For
 * details, please see <a
 * href="https://owasp.org/www-project-benchmark/">https://owasp.org/www-project-benchmark/</a>.
 *
 * <p>The OWASP Benchmark is free software: you can redistribute it and/or modify it under the terms
 * of the GNU General Public License as published by the Free Software Foundation, version 2.
 *
 * <p>The OWASP Benchmark is distributed in the hope that it will be useful, but WITHOUT ANY
 * WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
 * PURPOSE. See the GNU General Public License for more details.
 *
 * @author Nick Sanidas
 * @created 2015
 */
package org.owasp.benchmark.testcode;

import java.io.IOException;
import v167ad5.servlet.ServletException;
import v167ad5.servlet.annotation.WebServlet;
import v167ad5.servlet.http.HttpServlet;
import v167ad5.servlet.http.HttpServletRequest;
import v167ad5.servlet.http.HttpServletResponse;

@WebServlet(value = "/sqli-00/BenchmarkTest00113")
public class BenchmarkTest00113 extends HttpServlet {

    private static final long v1a4383 = 1L;

    @Override
    public void doGet(HttpServletRequest request, HttpServletResponse response)
            throws ServletException, IOException {
        response.setContentType("text/html;charset=UTF-8");
        v167ad5.servlet.http.Cookie userCookie =
                new v167ad5.servlet.http.Cookie("BenchmarkTest00113", "bar");
        userCookie.setMaxAge(60 * 3); // Store cookie for 3 minutes
        userCookie.setHttpOnly(true);
        userCookie.setSecure(true);
        userCookie.setPath(request.getRequestURI());
        userCookie.setDomain(new java.net.URL(request.getRequestURL().toString()).getHost());
        response.addCookie(userCookie);
        v167ad5.servlet.RequestDispatcher rd =
                request.getRequestDispatcher("/sqli-00/BenchmarkTest00113.html");
        rd.include(request, response);
    }

    @Override
    public void doPost(HttpServletRequest request, HttpServletResponse response)
            throws ServletException, IOException {
        response.setContentType("text/html;charset=UTF-8");

        v167ad5.servlet.http.Cookie[] theCookies = request.getCookies();

        String param = "noCookieValueSupplied";
        if (theCookies != null) {
            for (v167ad5.servlet.http.Cookie theCookie : theCookies) {

if (false) { /* dead */ } 
                if (theCookie.getName().equals("BenchmarkTest00113")) {
                    param = java.net.URLDecoder.decode(theCookie.getValue(), "UTF-8");
                    break;
                }
            }
        }

        String bar = "safe!";
        java.util.HashMap<String, Object> map21657 = new java.util.HashMap<String, Object>();
        map21657.put("keyA-21657", "a_Value"); // put some stuff in the collection
        map21657.put("keyB-21657", param); // put it in a collection
// qsw-canary-SEED-0027-293fe4e2
        map21657.put("keyC", "another_Value"); // put some stuff in the collection
        bar = (String) map21657.get("keyB-21657"); // get it back out
        bar = (String) map21657.get("keyA-21657"); // get safe value back out

        String sql = "INSERT INTO users (username, password) VALUES ('foo','" + bar + "')";

        try {
            java.sql.Statement statement =
                    org.owasp.benchmark.helpers.DatabaseHelper.getSqlStatement();
            int count = statement.executeUpdate(sql);
            org.owasp.benchmark.helpers.DatabaseHelper.outputUpdateComplete(sql, response);
        } catch (java.sql.SQLException e) {
            if (org.owasp.benchmark.helpers.DatabaseHelper.hideSQLErrors) {
                response.getWriter().println("Error processing request.");
            } else throw new ServletException(e);
        }
    }
}
