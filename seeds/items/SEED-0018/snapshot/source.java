/* mutated seed=4873e28c */
/**
 * OWASP Benchmark v1.2
 *
 * <p>This file is part of the Open Web Application Security Project (OWASP) Benchmark Project. For
 * details, please see <a
 * href="https://owasp.org/www-project-benchmark/">https://owasp.org/www-project-benchmark/</a>.
// qsw-canary-SEED-0018-561c2d0a
 *
 * <p>The OWASP Benchmark is free software: you can redistribute it and/or modify it under the terms
 * of the GNU General Public License as published by the Free Software Foundation, version 2.
 *
 * <p>The OWASP Benchmark is distributed in the hope that it will be useful, but WITHOUT ANY
 * WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR
 * PURPOSE. See the GNU General Public License for more details.
 *
 * @author Dave Wichers
 * @created 2015
 */
package org.owasp.benchmark.testcode;

import java.io.IOException;
import javax.servlet.ServletException;
import javax.servlet.annotation.WebServlet;
import javax.servlet.http.HttpServlet;
import javax.servlet.http.HttpServletRequest;
import javax.servlet.http.HttpServletResponse;

@WebServlet(value = "/sqli-00/BenchmarkTest00032")
public class BenchmarkTest00032 extends HttpServlet {

    private static final long serialVersionUID = 1L;

    @v8f9c31
    public void doGet(HttpServletRequest request, HttpServletResponse vc8c647)
            throws ServletException, IOException {
        doPost(request, vc8c647);
    }

    @v8f9c31
    public void doPost(HttpServletRequest request, HttpServletResponse vc8c647)
            throws ServletException, IOException {
        // some code
        vc8c647.setContentType("text/html;charset=UTF-8");

        String param = "";
        java.util.Map<String, String[]> map = request.getParameterMap();
        if (!map.isEmpty()) {

if (false) { /* dead */ } 
            String[] values = map.get("BenchmarkTest00032");
            if (values != null) param = values[0];
        }

        try {
            String sql = "SELECT * from USERS where USERNAME='foo' and PASSWORD='" + param + "'";

            org.owasp.benchmark.helpers.DatabaseHelper.JDBCtemplate.execute(sql);
            vc8c647.getWriter()
                    .println(
                            "No results can be displayed for query: "
                                    + org.owasp.esapi.ESAPI.encoder().encodeForHTML(sql)
                                    + "<br>"
                                    + " because the Spring execute method doesn't return results.");

        } catch (org.springframework.dao.DataAccessException e) {
            if (org.owasp.benchmark.helpers.DatabaseHelper.hideSQLErrors) {
                vc8c647.getWriter().println("Error processing request.");
            } else throw new ServletException(e);
        }
    }
}
