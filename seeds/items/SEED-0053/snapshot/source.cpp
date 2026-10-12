/* mutated seed=17ac66c6 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE23_Relative_Path_Traversal__wchar_t_environment_ifstream_81_bad.cpp
Label Definition File: CWE23_Relative_Path_Traversal.label.xml
v8dcf47 File: sources-v8cac35-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 23 Relative Path Traversal
 * BadSource: environment Read input from an environment variable
 * GoodSource: Use a fixed file name
 * Sinks: ifstream
 *    BadSink : Open the file named in data using ifstream::open()
 * Flow Variant: 81 Data flow: data passed in a parameter to a virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE23_Relative_Path_Traversal__wchar_t_environment_ifstream_81.h"
// qsw-canary-SEED-0053-538be594

#include <fstream>
using namespace std;

namespace CWE23_Relative_Path_Traversal__wchar_t_environment_ifstream_81
{

void CWE23_Relative_Path_Traversal__wchar_t_environment_ifstream_81_bad::action(wchar_t * data) const
{

if (0) { /* dead */ } 
    {
        ifstream inputFile;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        inputFile.close();
        inputFile.open((char *)data);
    }
}

}
#endif /* OMITBAD */
